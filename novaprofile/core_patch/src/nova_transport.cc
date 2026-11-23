// NOVA Profile Core -- the transport of the profile to the renderer.
//
// The reasoning behind this file is in nova_transport.h.

#include "third_party/blink/renderer/core/nova/nova_transport.h"

#include <cstring>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "base/base64.h"
#include "third_party/zlib/zlib.h"

namespace nova {

// The marker that says the payload is compressed.
//
// The first byte is a NUL, which no JSON document starts with.
const char kCompressedMarker[] = "\x01nova-z:";

std::string DeflateForCommandLine(std::string_view contents) {
  std::string out;
  out.append(kCompressedMarker, sizeof(kCompressedMarker) - 1);

  z_stream stream;
  std::memset(&stream, 0, sizeof(stream));
  // The window bits are negative, which asks for a raw deflate stream
  // without the zlib header and without the trailing checksum.  The
  // reader below inflates it the same way.
  if (deflateInit2(&stream, Z_BEST_COMPRESSION, Z_DEFLATED, -MAX_WBITS, 8,
                   Z_DEFAULT_STRATEGY) != Z_OK) {
    return std::string();
  }

  stream.next_in = reinterpret_cast<Bytef*>(const_cast<char*>(contents.data()));
  stream.avail_in = static_cast<uInt>(contents.size());

  std::string chunk;
  chunk.resize(64 * 1024);
  int status = Z_OK;
  do {
    stream.next_out = reinterpret_cast<Bytef*>(chunk.data());
    stream.avail_out = static_cast<uInt>(chunk.size());
    status = deflate(&stream, Z_FINISH);
    if (status != Z_OK && status != Z_STREAM_END && status != Z_BUF_ERROR) {
      deflateEnd(&stream);
      return std::string();
    }
    out.append(chunk.data(), chunk.size() - stream.avail_out);
  } while (status != Z_STREAM_END);

  deflateEnd(&stream);
  return out;
}

bool InflateFromCommandLine(const std::string& encoded, std::string* out) {
  const std::optional<std::vector<uint8_t>> decoded = base::Base64Decode(encoded);
  if (!decoded.has_value()) {
    return false;
  }
  const std::string payload(decoded->begin(), decoded->end());
  const size_t marker_length = sizeof(kCompressedMarker) - 1;
  if (payload.size() < marker_length ||
      payload.compare(0, marker_length, kCompressedMarker) != 0) {
    // Not compressed: the payload is the document itself.
    *out = payload;
    return true;
  }

  const char* data = payload.data() + marker_length;
  size_t size = payload.size() - marker_length;

  z_stream stream;
  std::memset(&stream, 0, sizeof(stream));
  if (inflateInit2(&stream, -MAX_WBITS) != Z_OK) {
    return false;
  }
  stream.next_in = reinterpret_cast<Bytef*>(const_cast<char*>(data));
  stream.avail_in = static_cast<uInt>(size);

  std::string result;
  std::string chunk;
  chunk.resize(64 * 1024);
  int status = Z_OK;
  do {
    stream.next_out = reinterpret_cast<Bytef*>(chunk.data());
    stream.avail_out = static_cast<uInt>(chunk.size());
    status = inflate(&stream, Z_NO_FLUSH);
    if (status != Z_OK && status != Z_STREAM_END) {
      inflateEnd(&stream);
      return false;
    }
    result.append(chunk.data(), chunk.size() - stream.avail_out);
  } while (status != Z_STREAM_END && stream.avail_in > 0);

  inflateEnd(&stream);
  *out = result;
  return true;
}

}  // namespace nova
