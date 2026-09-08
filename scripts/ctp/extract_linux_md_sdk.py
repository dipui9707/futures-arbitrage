#!/usr/bin/env python3
"""Extract only the Linux x86-64 CTP mduserapi SDK from the vendor archive."""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path, PurePosixPath
import tarfile
from zipfile import ZipFile


REQUIRED_NAMES = (
    "ThostFtdcMdApi.h",
    "ThostFtdcUserApiDataType.h",
    "ThostFtdcUserApiStruct.h",
)
LIBRARY_NAMES = ("thostmduserapi_se.so", "libthostmduserapi.so")
OPTIONAL_NAMES = ("error.xml", "error.dtd")


def decoded_name(name: str) -> str:
    try:
        return name.encode("cp437").decode("gb18030")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def extract_tar(
    content: bytes, mode: str, destination: Path, *, require_mdapi_path: bool
) -> list[Path]:
    wanted = set(REQUIRED_NAMES + LIBRARY_NAMES + OPTIONAL_NAMES)
    extracted: list[Path] = []
    with tarfile.open(fileobj=BytesIO(content), mode=mode) as sdk:
        for member in sdk.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"unsafe archive member: {member.name}")
            if not member.isfile() or path.name not in wanted:
                continue
            if not require_mdapi_path or "mdapi" in path.parts or path.name in OPTIONAL_NAMES:
                source = sdk.extractfile(member)
                if source is None:
                    continue
                target = destination / path.name
                target.write_bytes(source.read())
                extracted.append(target)
    return extracted


def extract(archive: Path, destination: Path) -> list[Path]:
    destination.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive) as outer:
        mini_tar = next(
            (
                entry for entry in outer.infolist()
                if "linux64" in decoded_name(entry.filename).lower()
                and decoded_name(entry.filename).lower().endswith(".tar.gz")
            ),
            None,
        )
        if mini_tar is not None:
            extracted = extract_tar(
                outer.read(mini_tar), "r:gz", destination, require_mdapi_path=True
            )
        else:
            md_entry = next(
                entry
                for entry in outer.infolist()
                if decoded_name(entry.filename).endswith("_mduserapi.zip")
            )
            with ZipFile(BytesIO(outer.read(md_entry))) as md_zip:
                tar_entry = next(
                    entry
                    for entry in md_zip.infolist()
                    if decoded_name(entry.filename).endswith("_linux64.tar")
                )
                extracted = extract_tar(
                    md_zip.read(tar_entry), "r:", destination, require_mdapi_path=False
                )
    names = {path.name for path in extracted}
    missing = set(REQUIRED_NAMES) - names
    if missing or not names.intersection(LIBRARY_NAMES):
        raise RuntimeError(
            f"missing SDK files: {sorted(missing or set(LIBRARY_NAMES))}"
        )
    return extracted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    for path in extract(args.archive, args.destination):
        print(path)


if __name__ == "__main__":
    main()
