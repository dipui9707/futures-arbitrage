#include "ThostFtdcMdApi.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <fstream>
#include <iostream>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace {

std::atomic<bool> running{true};

void on_signal(int) { running = false; }

std::string env_required(const char *name) {
    const char *value = std::getenv(name);
    if (value == nullptr || *value == '\0') {
        throw std::runtime_error(std::string("missing environment variable: ") + name);
    }
    return value;
}

std::string env_or(const char *name, const char *fallback) {
    const char *value = std::getenv(name);
    return value == nullptr || *value == '\0' ? fallback : value;
}

int env_int(const char *name, int fallback) {
    const char *value = std::getenv(name);
    return value == nullptr || *value == '\0' ? fallback : std::stoi(value);
}

std::vector<std::string> read_instruments(const std::string &path) {
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("cannot open instrument file: " + path);
    }
    std::vector<std::string> instruments;
    std::string line;
    while (std::getline(input, line)) {
        const auto first = line.find_first_not_of(" \t\r\n");
        if (first == std::string::npos || line[first] == '#') {
            continue;
        }
        const auto last = line.find_last_not_of(" \t\r\n");
        instruments.push_back(line.substr(first, last - first + 1));
    }
    if (instruments.empty()) {
        throw std::runtime_error("instrument file is empty: " + path);
    }
    return instruments;
}

std::string json_escape(const char *value) {
    std::ostringstream out;
    for (const unsigned char c : std::string(value == nullptr ? "" : value)) {
        switch (c) {
        case '"': out << "\\\""; break;
        case '\\': out << "\\\\"; break;
        case '\b': out << "\\b"; break;
        case '\f': out << "\\f"; break;
        case '\n': out << "\\n"; break;
        case '\r': out << "\\r"; break;
        case '\t': out << "\\t"; break;
        default:
            if (c < 0x20) {
                out << "?";
            } else {
                out << c;
            }
        }
    }
    return out.str();
}

void append_number(std::ostringstream &out, const char *name, double value) {
    out << ",\"" << name << "\":";
    if (std::isfinite(value) && std::abs(value) < 1e100) {
        out.precision(15);
        out << value;
    } else {
        out << "null";
    }
}

bool send_all(int fd, const std::string &message) {
    std::size_t offset = 0;
    while (offset < message.size()) {
        const auto sent = ::send(fd, message.data() + offset, message.size() - offset, MSG_NOSIGNAL);
        if (sent <= 0) {
            return false;
        }
        offset += static_cast<std::size_t>(sent);
    }
    return true;
}

class StreamServer {
public:
    StreamServer(std::string host, int port, std::size_t backlog_limit)
        : host_(std::move(host)), port_(port), backlog_limit_(backlog_limit) {}

    ~StreamServer() { stop(); }

    void start() {
        listener_ = ::socket(AF_INET, SOCK_STREAM, 0);
        if (listener_ < 0) {
            throw std::runtime_error("cannot create stream socket");
        }
        int enabled = 1;
        ::setsockopt(listener_, SOL_SOCKET, SO_REUSEADDR, &enabled, sizeof(enabled));
        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_port = htons(static_cast<uint16_t>(port_));
        if (::inet_pton(AF_INET, host_.c_str(), &address.sin_addr) != 1 ||
            ::bind(listener_, reinterpret_cast<sockaddr *>(&address), sizeof(address)) != 0 ||
            ::listen(listener_, 4) != 0) {
            ::close(listener_);
            listener_ = -1;
            throw std::runtime_error("cannot listen on " + host_ + ":" + std::to_string(port_));
        }
        accepting_ = true;
        accept_thread_ = std::thread([this] { accept_loop(); });
        std::cerr << "stream listening on " << host_ << ':' << port_ << '\n';
    }

    std::uint64_t next_sequence() { return ++sequence_; }

    void publish(std::uint64_t sequence, std::string message) {
        message.push_back('\n');
        std::lock_guard<std::mutex> lock(io_mutex_);
        backlog_.emplace_back(sequence, message);
        while (backlog_.size() > backlog_limit_) {
            backlog_.pop_front();
        }
        if (client_ >= 0 && !send_all(client_, message)) {
            ::close(client_);
            client_ = -1;
            std::cerr << "collector disconnected\n";
        }
    }

    void status(const std::string &state, int reason = 0) {
        const auto sequence = next_sequence();
        std::ostringstream out;
        out << "{\"type\":\"status\",\"seq\":" << sequence
            << ",\"state\":\"" << json_escape(state.c_str()) << "\",\"reason\":" << reason << '}';
        publish(sequence, out.str());
    }

    void stop() {
        accepting_ = false;
        if (listener_ >= 0) {
            ::shutdown(listener_, SHUT_RDWR);
            ::close(listener_);
            listener_ = -1;
        }
        if (accept_thread_.joinable()) {
            accept_thread_.join();
        }
        std::lock_guard<std::mutex> lock(io_mutex_);
        if (client_ >= 0) {
            ::close(client_);
            client_ = -1;
        }
    }

private:
    void accept_loop() {
        while (accepting_) {
            sockaddr_in peer{};
            socklen_t size = sizeof(peer);
            const int incoming = ::accept(listener_, reinterpret_cast<sockaddr *>(&peer), &size);
            if (incoming < 0) {
                if (accepting_) {
                    std::this_thread::sleep_for(std::chrono::seconds(1));
                }
                continue;
            }
            timeval timeout{2, 0};
            ::setsockopt(incoming, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
            char request[128]{};
            const auto received = ::recv(incoming, request, sizeof(request) - 1, 0);
            std::uint64_t resume_after = 0;
            if (received > 0) {
                std::istringstream parser(std::string(request, static_cast<std::size_t>(received)));
                std::string command;
                parser >> command >> resume_after;
                if (command != "RESUME") {
                    resume_after = 0;
                }
            }
            std::lock_guard<std::mutex> lock(io_mutex_);
            if (client_ >= 0) {
                ::close(client_);
            }
            client_ = incoming;
            bool replay_ok = true;
            for (const auto &[sequence, message] : backlog_) {
                if (sequence > resume_after && !send_all(client_, message)) {
                    replay_ok = false;
                    break;
                }
            }
            if (!replay_ok) {
                ::close(client_);
                client_ = -1;
            } else {
                std::cerr << "collector connected; resume_after=" << resume_after << '\n';
            }
        }
    }

    std::string host_;
    int port_;
    std::size_t backlog_limit_;
    int listener_{-1};
    int client_{-1};
    std::atomic<bool> accepting_{false};
    std::atomic<std::uint64_t> sequence_{0};
    std::thread accept_thread_;
    std::mutex io_mutex_;
    std::deque<std::pair<std::uint64_t, std::string>> backlog_;
};

class MarketSpi final : public CThostFtdcMdSpi {
public:
    MarketSpi(CThostFtdcMdApi *api, StreamServer &stream, std::string broker,
              std::string user, std::string password, std::vector<std::string> instruments)
        : api_(api), stream_(stream), broker_(std::move(broker)), user_(std::move(user)),
          password_(std::move(password)), instruments_(std::move(instruments)) {}

    void OnFrontConnected() override {
        std::cerr << "CTP front connected; requesting login\n";
        stream_.status("front_connected");
        CThostFtdcReqUserLoginField request{};
        copy(request.BrokerID, broker_);
        copy(request.UserID, user_);
        copy(request.Password, password_);
        const int result = api_->ReqUserLogin(&request, ++request_id_);
        if (result != 0) {
            std::cerr << "ReqUserLogin rejected locally: " << result << '\n';
        }
    }

    void OnFrontDisconnected(int reason) override {
        logged_in_ = false;
        std::cerr << "CTP front disconnected: " << reason << '\n';
        stream_.status("front_disconnected", reason);
    }

    void OnHeartBeatWarning(int lapse) override {
        std::cerr << "CTP heartbeat warning: " << lapse << '\n';
    }

    void OnRspUserLogin(CThostFtdcRspUserLoginField *, CThostFtdcRspInfoField *info,
                        int, bool last) override {
        if (!last) {
            return;
        }
        if (has_error(info, "login")) {
            stream_.status("login_failed", info == nullptr ? -1 : info->ErrorID);
            return;
        }
        logged_in_ = true;
        std::vector<char *> ids;
        ids.reserve(instruments_.size());
        for (auto &instrument : instruments_) {
            ids.push_back(instrument.data());
        }
        const int result = api_->SubscribeMarketData(ids.data(), static_cast<int>(ids.size()));
        std::cerr << "CTP login succeeded; subscribing " << ids.size() << " instruments\n";
        stream_.status("logged_in");
        if (result != 0) {
            std::cerr << "SubscribeMarketData rejected locally: " << result << '\n';
        }
    }

    void OnRspSubMarketData(CThostFtdcSpecificInstrumentField *instrument,
                            CThostFtdcRspInfoField *info, int, bool last) override {
        if (has_error(info, "subscribe")) {
            return;
        }
        if (instrument != nullptr) {
            ++subscriptions_;
        }
        if (last) {
            std::cerr << "subscription responses completed; accepted=" << subscriptions_ << '\n';
            stream_.status("subscribed");
        }
    }

    void OnRspError(CThostFtdcRspInfoField *info, int request_id, bool) override {
        if (info != nullptr) {
            std::cerr << "CTP response error request=" << request_id << " id=" << info->ErrorID
                      << " message=" << info->ErrorMsg << '\n';
        }
    }

    void OnRtnDepthMarketData(CThostFtdcDepthMarketDataField *tick) override {
        if (!logged_in_ || tick == nullptr || tick->InstrumentID[0] == '\0') {
            return;
        }
        const auto sequence = stream_.next_sequence();
        std::ostringstream out;
        out << "{\"type\":\"tick\",\"seq\":" << sequence
            << ",\"instrument\":\"" << json_escape(tick->InstrumentID) << '"'
            << ",\"exchange\":\"" << json_escape(tick->ExchangeID) << '"'
            << ",\"trading_day\":\"" << json_escape(tick->TradingDay) << '"'
            << ",\"action_day\":\"" << json_escape(tick->ActionDay) << '"'
            << ",\"update_time\":\"" << json_escape(tick->UpdateTime) << '"'
            << ",\"update_millisec\":" << tick->UpdateMillisec
            << ",\"volume\":" << tick->Volume;
        append_number(out, "last_price", tick->LastPrice);
        append_number(out, "pre_close", tick->PreClosePrice);
        append_number(out, "open_price", tick->OpenPrice);
        append_number(out, "highest_price", tick->HighestPrice);
        append_number(out, "lowest_price", tick->LowestPrice);
        append_number(out, "turnover", tick->Turnover);
        append_number(out, "open_interest", tick->OpenInterest);
        append_number(out, "bid_price1", tick->BidPrice1);
        out << ",\"bid_volume1\":" << tick->BidVolume1;
        append_number(out, "ask_price1", tick->AskPrice1);
        out << ",\"ask_volume1\":" << tick->AskVolume1 << '}';
        stream_.publish(sequence, out.str());
    }

private:
    template <std::size_t Size>
    static void copy(char (&target)[Size], const std::string &value) {
        std::strncpy(target, value.c_str(), Size - 1);
        target[Size - 1] = '\0';
    }

    static bool has_error(CThostFtdcRspInfoField *info, const char *operation) {
        if (info == nullptr || info->ErrorID == 0) {
            return false;
        }
        std::cerr << "CTP " << operation << " error id=" << info->ErrorID
                  << " message=" << info->ErrorMsg << '\n';
        return true;
    }

    CThostFtdcMdApi *api_;
    StreamServer &stream_;
    std::string broker_;
    std::string user_;
    std::string password_;
    std::vector<std::string> instruments_;
    std::atomic<int> request_id_{0};
    std::atomic<int> subscriptions_{0};
    std::atomic<bool> logged_in_{false};
};

} // namespace

int main(int argc, char **argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--version") {
            std::cout << CThostFtdcMdApi::GetApiVersion() << '\n';
            return 0;
        }
        std::signal(SIGINT, on_signal);
        std::signal(SIGTERM, on_signal);
        std::signal(SIGPIPE, SIG_IGN);

        const auto front = env_required("CTP_MD_FRONT");
        const auto broker = env_required("CTP_BROKER_ID");
        const auto user = env_required("CTP_USER_ID");
        const auto password = env_required("CTP_PASSWORD");
        const auto instruments = read_instruments(env_required("CTP_INSTRUMENT_FILE"));
        const auto listen_host = env_or("CTP_LISTEN_HOST", "127.0.0.1");
        const auto listen_port = env_int("CTP_LISTEN_PORT", 19001);
        const auto backlog_limit = static_cast<std::size_t>(env_int("CTP_REPLAY_TICKS", 10000));
        const auto flow_path = env_or("CTP_FLOW_PATH", "/var/lib/ctp-md/flow/");

        StreamServer stream(listen_host, listen_port, backlog_limit);
        stream.start();
        CThostFtdcMdApi *api = CThostFtdcMdApi::CreateFtdcMdApi(flow_path.c_str(), false, false);
        if (api == nullptr) {
            throw std::runtime_error("CreateFtdcMdApi returned null");
        }
        MarketSpi spi(api, stream, broker, user, password, instruments);
        api->RegisterSpi(&spi);
        std::vector<char> front_buffer(front.begin(), front.end());
        front_buffer.push_back('\0');
        api->RegisterFront(front_buffer.data());
        api->Init();
        while (running) {
            std::this_thread::sleep_for(std::chrono::seconds(1));
        }
        api->RegisterSpi(nullptr);
        api->Release();
        stream.stop();
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "fatal: " << error.what() << '\n';
        return 1;
    }
}
