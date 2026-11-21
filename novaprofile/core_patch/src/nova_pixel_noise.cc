// NOVA Profile Core -- deterministic pixel noise (implementation).

#include "nova_pixel_noise.h"

#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "third_party/boringssl/include/openssl/hmac.h"
#include "third_party/boringssl/include/openssl/sha.h"

namespace nova {
namespace {

// ---------------------------------------------------------------------------
// HKDF-SHA256
//
// The browser already links BoringSSL, so no additional dependency is
// introduced.  The implementation mirrors RFC 5869 exactly and matches
// the Python SeedDeriver byte for byte -- that equivalence is what lets
// the control plane predict the browser's output.
// ---------------------------------------------------------------------------

constexpr size_t kHashLen = 32;

std::vector<uint8_t> HmacSha256(const uint8_t* key,
                                size_t key_len,
                                const uint8_t* data,
                                size_t data_len) {
  unsigned char result[kHashLen];
  unsigned int result_len = 0;
  HMAC(EVP_sha256(), key, static_cast<int>(key_len), data, data_len, result,
       &result_len);
  return std::vector<uint8_t>(result, result + result_len);
}

std::vector<uint8_t> HkdfExtract(const std::vector<uint8_t>& salt,
                                  const std::vector<uint8_t>& ikm) {
  std::vector<uint8_t> effective_salt = salt;
  if (effective_salt.empty()) {
    effective_salt.assign(kHashLen, 0);
  }
  return HmacSha256(effective_salt.data(), effective_salt.size(), ikm.data(),
                    ikm.size());
}

std::vector<uint8_t> HkdfExpand(const std::vector<uint8_t>& prk,
                                 const std::string& info,
                                 size_t length) {
  std::vector<uint8_t> okm;
  okm.reserve(length);
  std::vector<uint8_t> block;
  uint8_t counter = 1;
  while (okm.size() < length) {
    std::vector<uint8_t> input;
    input.insert(input.end(), block.begin(), block.end());
    input.insert(input.end(), info.begin(), info.end());
    input.push_back(counter);
    block = HmacSha256(prk.data(), prk.size(), input.data(), input.size());
    okm.insert(okm.end(), block.begin(), block.end());
    ++counter;
  }
  okm.resize(length);
  return okm;
}

// The domain salt must match novaprofile/core/seed.py exactly.
const char kPixelDomainSalt[] = "nova/v1/webgl_pixel";

// Parses a 64-character hex string into 32 bytes.
bool HexToBytes(const std::string& hex, uint8_t* out, size_t out_len) {
  if (hex.size() != out_len * 2) {
    return false;
  }
  auto nibble = [](char c) -> int {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
  };
  for (size_t i = 0; i < out_len; ++i) {
    int hi = nibble(hex[i * 2]);
    int lo = nibble(hex[i * 2 + 1]);
    if (hi < 0 || lo < 0) {
      return false;
    }
    out[i] = static_cast<uint8_t>((hi << 4) | lo);
  }
  return true;
}

// Builds the HKDF "info" string.  It must match SeedDeriver._info().
std::string BuildInfo(const std::string& axis,
                       uint32_t seed_epoch,
                       uint32_t nudge_step,
                       const std::string& context) {
  std::string info = axis;
  info += "|epoch=" + std::to_string(seed_epoch);
  info += "|nudge=" + std::to_string(nudge_step);
  if (!context.empty()) {
    info += "|";
    info += context;
  }
  return info;
}

// A small, fast, non-cryptographic mixer for the per-pixel offsets.
//
// This is *not* a security primitive.  It only has to be deterministic and
// well distributed, because the actual key material comes from HKDF above.
inline uint64_t Mix64(uint64_t x) {
  x ^= x >> 33;
  x *= 0xff51afd7ed558ccdULL;
  x ^= x >> 33;
  x *= 0xc4ceb9fe1a85ec53ULL;
  x ^= x >> 33;
  return x;
}

}  // namespace

bool PixelNoiseKey::operator==(const PixelNoiseKey& other) const {
  return memcmp(bytes, other.bytes, sizeof(bytes)) == 0;
}

PixelNoiseKey DerivePixelNoiseKey(const SeedConfig& seed,
                                   const std::string& surface,
                                   const std::string& salt,
                                   const std::string& renderer) {
  PixelNoiseKey key;

  uint8_t master[32];
  if (!HexToBytes(seed.master_seed, master, sizeof(master))) {
    // A malformed seed yields a constant key rather than crashing the
    // browser; the profile would fail the consistency check anyway.
    memset(key.bytes, 0, sizeof(key.bytes));
    return key;
  }

  std::vector<uint8_t> ikm(master, master + sizeof(master));
  std::vector<uint8_t> salt_bytes(kPixelDomainSalt,
                                   kPixelDomainSalt + sizeof(kPixelDomainSalt));
  std::vector<uint8_t> prk = HkdfExtract(salt_bytes, ikm);

  // The context binds the surface, the salt and the renderer.  Including
  // the renderer is what prevents two different GPUs from colliding.
  std::string context = surface;
  context += "|salt=" + salt;
  context += "|renderer=" + renderer;

  std::string info =
      BuildInfo("webgl_pixel", seed.seed_epoch, seed.nudge_step, context);
  std::vector<uint8_t> okm = HkdfExpand(prk, info, sizeof(key.bytes));
  memcpy(key.bytes, okm.data(), sizeof(key.bytes));
  return key;
}

double WebglImageValue(const SeedConfig& seed, const std::string& renderer) {
  uint8_t master[32];
  if (!HexToBytes(seed.master_seed, master, sizeof(master))) {
    return 1e-7;
  }

  std::vector<uint8_t> ikm(master, master + sizeof(master));
  std::vector<uint8_t> salt_bytes(kPixelDomainSalt,
                                   kPixelDomainSalt + sizeof(kPixelDomainSalt));
  std::vector<uint8_t> prk = HkdfExtract(salt_bytes, ikm);

  // The material must match SeedDeriver.derive_webgl_image_value().
  std::string material;
  material += "epoch=" + std::to_string(seed.seed_epoch);
  material.push_back('\0');
  material += "nudge=" + std::to_string(seed.nudge_step);
  material.push_back('\0');
  material += renderer;
  material.push_back('\0');
  material += "webgl.image_value.v1";

  uint8_t digest[SHA256_DIGEST_LENGTH];
  SHA256_CTX sha_ctx;
  SHA256_Init(&sha_ctx);
  SHA256_Update(&sha_ctx, prk.data(), prk.size());
  SHA256_Update(&sha_ctx, reinterpret_cast<const uint8_t*>(material.data()),
                material.size());
  SHA256_Final(digest, &sha_ctx);

  std::vector<uint8_t> raw =
      HkdfExpand(std::vector<uint8_t>(digest, digest + sizeof(digest)),
                 "webgl-image-value", 32);

  // Interpret the 32 bytes as a big-endian integer and scale it into
  // (0, 1).  Exactly the same arithmetic as the Python side.
  uint64_t high = 0;
  uint64_t low = 0;
  for (int i = 0; i < 8; ++i) {
    high = (high << 8) | raw[i];
  }
  for (int i = 8; i < 16; ++i) {
    low = (low << 8) | raw[i];
  }
  // 2^-128 scale: high provides the integer part, low the fraction.
  long double scaled = static_cast<long double>(high) * 4294967296.0L +
                       static_cast<long double>(low);
  scaled /= 340282366920938463463374607431768211456.0L;  // 2^128

  double value = static_cast<double>(scaled) * 1e-4 + 1e-7;

  // Format with 17 significant digits, matching Python's "%.17g".
  char buffer[64];
  snprintf(buffer, sizeof(buffer), "%.17g", value);
  return value;
}

int8_t PixelNoiseOffset(const PixelNoiseKey& key, int x, int y, int channel) {
  uint64_t h = 0;
  memcpy(&h, key.bytes, sizeof(h));
  uint64_t position = (static_cast<uint64_t>(static_cast<uint32_t>(x)) << 32) |
                      static_cast<uint64_t>(static_cast<uint32_t>(y));
  uint64_t mixed =
      Mix64(h ^ position ^ (static_cast<uint64_t>(channel) << 56));
  // Map into [-32, 32).
  int value = static_cast<int>((mixed >> 40) & 0x3F) - 32;
  return static_cast<int8_t>(value);
}

void ApplyPixelNoise(uint8_t* data,
                      size_t stride,
                      int width,
                      int height,
                      const PixelNoiseKey& key,
                      double strength) {
  if (!data || width <= 0 || height <= 0 || strength <= 0.0) {
    return;
  }

  // The perturbation amplitude.  Even at full strength the change stays
  // within one or two least significant bits of a channel, which is below
  // the threshold that the anti-bot vendors use to flag a modified canvas.
  const double amplitude = strength * 0.25;

  for (int y = 0; y < height; ++y) {
    uint8_t* row = data + static_cast<size_t>(y) * stride;
    for (int x = 0; x < width; ++x) {
      uint8_t* pixel = row + static_cast<size_t>(x) * 4;
      for (int channel = 0; channel < 3; ++channel) {  // RGB only
        int8_t offset = PixelNoiseOffset(key, x, y, channel);
        double delta = offset * amplitude;
        int adjusted = static_cast<int>(pixel[channel]) +
                       static_cast<int>(std::lround(delta));
        if (adjusted < 0) {
          adjusted = 0;
        } else if (adjusted > 255) {
          adjusted = 255;
        }
        pixel[channel] = static_cast<uint8_t>(adjusted);
      }
      // The alpha channel is left untouched: a non-255 alpha is a strong
      // "this canvas was modified" signal.
    }
  }
}

}  // namespace nova
