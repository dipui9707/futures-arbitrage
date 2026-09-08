CXX ?= g++
SDK_DIR ?= vendor/linux64
BUILD_DIR ?= build
SDK_LIBRARY ?= thostmduserapi_se.so

CXXFLAGS := -O2 -std=c++17 -Wall -Wextra -Wpedantic -pthread -I$(SDK_DIR)
LDFLAGS := -pthread -L$(SDK_DIR) -Wl,-rpath,'$$ORIGIN/../lib' -Wl,-l:$(SDK_LIBRARY)

.PHONY: all clean

all: $(BUILD_DIR)/ctp-md-gateway

$(BUILD_DIR)/ctp-md-gateway: src/main.cpp $(SDK_DIR)/ThostFtdcMdApi.h
	mkdir -p $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) src/main.cpp -o $@ $(LDFLAGS)

clean:
	rm -r $(BUILD_DIR)
