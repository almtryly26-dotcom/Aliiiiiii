# SPDX-FileCopyrightText: 2026 Epic Games, Inc.
# SPDX-License-Identifier: MIT
"""Minimal gRPC client for the server's AdminService.ServerInfo RPC.

The server reports the cargo features it was compiled with via
`ServerInfoResponse.features`. Tests that depend on an optional feature (for
example `failure_generator`, which enables fault injection) use this to skip
cleanly when run against a server that wasn't built with the feature.

We avoid generated protobuf stubs: `ServerInfoRequest` is empty (zero bytes on
the wire) and we only need the repeated-string `features` field (field 2), so we
serialize an empty request and hand-parse that one field out of the response.
The gRPC AdminService is registered without an auth interceptor and the test
server runs gRPC in plaintext, so an insecure channel with no metadata works.
"""

import logging

import grpc

logger = logging.getLogger(__name__)

_SERVER_INFO_METHOD = "/urc.rpc.AdminService/ServerInfo"
_FEATURES_FIELD = 2


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Decode a base-128 varint at `pos`; return (value, next_pos)."""
    result = 0
    shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _parse_features(response: bytes) -> list[str]:
    """Extract repeated-string field 2 (`features`) from a ServerInfoResponse,
    skipping every other field so we stay forward-compatible with new fields."""
    features: list[str] = []
    pos = 0
    while pos < len(response):
        tag, pos = _read_varint(response, pos)
        field_number = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:  # varint
            _, pos = _read_varint(response, pos)
        elif wire_type == 1:  # 64-bit
            pos += 8
        elif wire_type == 2:  # length-delimited
            length, pos = _read_varint(response, pos)
            chunk = response[pos : pos + length]
            pos += length
            if field_number == _FEATURES_FIELD:
                features.append(chunk.decode("utf-8"))
        elif wire_type == 5:  # 32-bit
            pos += 4
        else:
            raise ValueError(f"Unsupported protobuf wire type {wire_type}")
    return features


def fetch_server_features(grpc_target: str, timeout: float = 10.0) -> set[str]:
    """Call AdminService.ServerInfo on `grpc_target` (host:port) and return the
    set of cargo features the server binary was compiled with."""
    with grpc.insecure_channel(grpc_target) as channel:
        call = channel.unary_unary(
            _SERVER_INFO_METHOD,
            request_serializer=lambda _request: b"",
            response_deserializer=_parse_features,
        )
        features = set(call(None, timeout=timeout))
    logger.info("Server %s reports compiled features: %s", grpc_target, sorted(features))
    return features
