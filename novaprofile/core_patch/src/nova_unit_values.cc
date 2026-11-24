// NOVA Profile Core -- the per-unit values, derived in the core.
//
// The profile file carries the *family* of a device: the model, the
// graphics part, the panel, the browser major.  Those are shared by
// every unit of the family, exactly as they are shared by every handset
// of that model in a shop.
//
// The values that a site uses to tell two *people* apart are not in
// that family.  The core count, the amount of memory, the number of
// touch points -- these are the readings that differ between two units
// that came out of the same factory, and they are what a script reads
// when it wants to know whether it is talking to a hundred thousand
// different handsets or to one handset wearing a hundred thousand
// hats.
//
// **Who decides the value**
//
// The control plane decides.  It draws the value from the profile's own
// seed, *stratified*, so that two units of one family never draw the
// same one; the stratum is what separates the units of a family, and
// it travels with the seed so that a profile that is closed and
// reopened comes back as the same unit.
//
// The core is the *authority*, not a second drawer.  It reads the
// value the control plane drew and serves it unchanged.  Two reasons:
//
//   1. The two sides have to agree.  The probe reads the browser and
//      compares the answer against what the control plane believes; a
//      core that drew its own value would disagree with the control
//      plane on every profile, and the report would say the whole
//      engine is broken.
//
//   2. The stratification has to be *total*.  The control plane knows
//      how many units a family carries, and it hands each of them its
//      own slice of the choice space.  The core sees one unit at a
//      time and cannot know how many there are, so a core that drew
//      independently would sometimes land on a value another unit
//      already has.
//
// The core does draw, in exactly one case: when the profile leaves a
// value out.  A profile that says nothing about its core count gets
// one from the hardware range, drawn from the seed the same way the
// control plane would have drawn it -- and the draw is the control
// plane's *algorithm*, reproduced here, so that the two sides answer
// the same question with the same number.
//
// **The algorithm**
//
// The draw is the control plane's ``derive_int``: a rejection-sampled
// uniform integer over the range, taken from an HKDF expansion whose
// ``info`` string carries the axis, the epoch, the nudge step, the
// stratum and the context.  The salt is the navigator's own salt, so
// the unit readings live on the navigator's axis -- which is where the
// control plane draws the values it does draw.
//
// Two axes never correlate, because the context is part of the info
// string: a fleet cannot be found out because the units that report
// eight cores are exactly the units that report four gigabytes.

#include "nova_unit_values.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <string>
#include <vector>

#include "third_party/boringssl/src/include/openssl/digest.h"
#include "third_party/boringssl/src/include/openssl/hmac.h"
#include "third_party/boringssl/src/include/openssl/sha.h"

namespace nova {
namespace {

constexpr size_t kHashLen = 32;

// The domain salt of the unit axis.
//
// It is the navigator's own salt, and it has to be: the control plane
// draws the core count and the memory on the navigator axis, and the
// core has to draw the same value from the same salt when the profile
// leaves the value out.  A separate salt would make the two sides
// disagree the moment the profile was incomplete.
const char kUnitDomainSalt[] = "nova/v1/navigator";

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

// The seed of a profile.
//
// The stratum is what separates two units of the *same* family, so it
// is folded into the derivation: without it, two units of one model
// would draw the same values.
struct UnitSeed {
  uint8_t master[32] = {};
  uint32_t epoch = 0;
  uint32_t stratum = 0;
  bool has_stratum = false;
  uint32_t nudge = 0;
  bool valid = false;
};

UnitSeed SeedOf(const SeedConfig& seed) {
  UnitSeed out;
  out.valid = HexToBytes(seed.master_seed, out.master, sizeof(out.master));
  out.epoch = seed.seed_epoch;
  out.stratum = seed.stratum;
  out.has_stratum = seed.has_stratum;
  out.nudge = seed.nudge_step;
  return out;
}

// The draw.
//
// It is the control plane's ``derive_int``: a uniform integer over the
// inclusive range, drawn by rejection sampling from an HKDF expansion.
//
// The rejection sampling is what makes the distribution exactly
// uniform.  A plain modulo would bias the low end of the range, and
// the low end of the range is where the narrow readings live: a fleet
// that reported more six-core handsets than the hardware of the model
// could account for would be a fleet a site could contradict.
//
// The draw counter is part of the context, so two calls with the same
// context return two different numbers -- which is what the control
// plane does, and what keeps the core from answering the same value
// twice for two different axes that share a context.
uint64_t DrawRaw(const UnitSeed& seed,
                 const std::string& context,
                 size_t length) {
  if (!seed.valid) {
    return 0;
  }
  std::vector<uint8_t> ikm(seed.master, seed.master + sizeof(seed.master));
  std::vector<uint8_t> salt(kUnitDomainSalt,
                             kUnitDomainSalt + sizeof(kUnitDomainSalt));
  std::vector<uint8_t> prk = HkdfExtract(salt, ikm);

  std::string info = "navigator";
  info += "|epoch=" + std::to_string(seed.epoch);
  info += "|nudge=" + std::to_string(seed.nudge);
  if (seed.has_stratum) {
    info += "|stratum=" + std::to_string(seed.stratum);
  }
  info += "|";
  info += context;

  std::vector<uint8_t> okm = HkdfExpand(prk, info, length);
  uint64_t value = 0;
  for (size_t i = 0; i < length && i < 8; ++i) {
    value = (value << 8) | okm[i];
  }
  return value;
}

// A uniform integer over the inclusive range [low, high].
//
// The counter is the draw counter of the control plane: it starts at
// zero and advances with every draw of the same context.
int DrawInt(const UnitSeed& seed,
            const std::string& context,
            int low,
            int high,
            uint32_t* counter) {
  if (low > high) {
    return low;
  }
  const uint64_t span = static_cast<uint64_t>(high - low) + 1;
  if (span == 1) {
    return low;
  }
  // The number of bytes the range needs, and the limit below which a
  // draw is accepted -- the largest multiple of the span that fits in
  // the byte count.
  size_t n_bytes = 1;
  while ((1ULL << (n_bytes * 8)) < span) {
    ++n_bytes;
  }
  const uint64_t ceiling = 1ULL << (n_bytes * 8);
  const uint64_t limit = ceiling - (ceiling % span);

  uint32_t attempt = *counter;
  uint32_t retry = 0;
  while (true) {
    std::string attempt_context = context;
    attempt_context += "|draw=" + std::to_string(attempt);
    attempt_context += "|retry=" + std::to_string(retry);
    const uint64_t value = DrawRaw(seed, attempt_context, n_bytes);
    if (value < limit) {
      *counter = attempt + 1;
      return low + static_cast<int>(value % span);
    }
    ++retry;
    ++attempt;
  }
}

// A uniform index into a list of options.
//
// The control plane draws an *index* into the option list, not a
// number out of a range: the two are the same only when the list is
// contiguous, and it is not always -- a model's memory options are
// four and eight, so a range of four to eight has five members and the
// list has two.  The draw therefore has to be the index, or the two
// sides would answer different questions.
int DrawIndex(const UnitSeed& seed,
              const std::string& context,
              size_t count,
              uint32_t* counter) {
  if (count == 0) {
    return 0;
  }
  return DrawInt(seed, context, 0, static_cast<int>(count) - 1, counter);
}

// The memory the platform reports.
//
// The platform clamps the value to a power of two and to eight
// gigabytes, because that is what ``navigator.deviceMemory`` can
// return.  A value outside that set is a tell on its own.
int QuantizeMemory(int gigabytes) {
  int value = gigabytes;
  if (value < 1) {
    value = 1;
  }
  if (value > 8) {
    value = 8;
  }
  // Round down to a power of two.
  int power = 1;
  while (power * 2 <= value) {
    power *= 2;
  }
  return power;
}

}  // namespace

UnitValues DeriveUnitValues(const ProfileConfig& config,
                             const std::vector<int>& preset_cores,
                             const std::vector<int>& preset_memory,
                             const std::vector<int>& preset_touch,
                             int preset_width,
                             int preset_height,
                             double preset_pixel_ratio,
                             const std::vector<double>& preset_ratios) {
  UnitValues values;
  const SeedConfig& seed = config.seed_config;
  const UnitSeed unit_seed = SeedOf(seed);

  // The draw counters of the control plane.  They are per context, and
  // the contexts below are the ones the control plane uses.
  uint32_t counter_cores = 0;
  uint32_t counter_memory = 0;
  uint32_t counter_touch = 0;

  bool derived = false;

  // -- the core count ------------------------------------------------------
  //
  // The value the control plane drew wins.  It is the one that is
  // stratified, and it is the one the probe compares against.
  if (config.navigator.hardware_concurrency > 0) {
    values.hardware_concurrency = config.navigator.hardware_concurrency;
  } else if (!preset_cores.empty()) {
    const int index = DrawIndex(unit_seed, "cores", preset_cores.size(), &counter_cores);
    values.hardware_concurrency = preset_cores[index];
    derived = true;
  } else {
    values.hardware_concurrency = 8;
    derived = true;
  }

  // -- the memory ----------------------------------------------------------
  if (config.navigator.device_memory > 0) {
    values.device_memory = config.navigator.device_memory;
  } else if (!preset_memory.empty()) {
    const int index = DrawIndex(unit_seed, "memory", preset_memory.size(), &counter_memory);
    values.device_memory = QuantizeMemory(preset_memory[index]);
    derived = true;
  } else {
    values.device_memory = 8;
    derived = true;
  }

  // -- the touch points ----------------------------------------------------
  //
  // The digitiser of the model decides the number of contacts.  Two
  // units of one model share a digitiser *class*, but a model that
  // shipped with both a five-point and a ten-point panel exists, and
  // the site reads the number.
  if (config.navigator.max_touch_points > 0) {
    values.max_touch_points = config.navigator.max_touch_points;
  } else if (!preset_touch.empty()) {
    const int index = DrawIndex(unit_seed, "touch", preset_touch.size(), &counter_touch);
    values.max_touch_points = preset_touch[index];
    derived = true;
  } else {
    values.max_touch_points = 5;
    derived = true;
  }

  // -- the panel ------------------------------------------------------------
  //
  // The resolution is a property of the *panel*, so it is not free: the
  // unit's panel is within a couple of pixels of the family's panel.
  // The jitter is what separates two units of the same model, and it is
  // tiny enough that the two cannot be told apart by eye.
  if (config.screen.width > 0 && config.screen.height > 0) {
    values.screen_width = config.screen.width;
    values.screen_height = config.screen.height;
  } else if (preset_width > 0 && preset_height > 0) {
    uint32_t counter_panel = 0;
    const int jitter_x = DrawInt(unit_seed, "panel_x", -2, 2, &counter_panel);
    const int jitter_y = DrawInt(unit_seed, "panel_y", -2, 2, &counter_panel);
    values.screen_width = std::max(1, preset_width + jitter_x);
    values.screen_height = std::max(1, preset_height + jitter_y);
    derived = true;
  } else {
    values.screen_width = 1080;
    values.screen_height = 2400;
    derived = true;
  }

  // -- the pixel ratio -----------------------------------------------------
  //
  // The ratio is a property of the panel and of the display scaling the
  // owner chose, so it is drawn from the small set the hardware can
  // produce -- the set the *model* offers, not a set of its own.
  //
  // A fixed list would be a list of ratios some other device offers,
  // and a model whose panels are all 2.625 that reports 3.5 is a
  // model with a panel it does not have.
  if (config.screen.device_pixel_ratio > 0) {
    values.device_pixel_ratio = config.screen.device_pixel_ratio;
  } else if (!preset_ratios.empty()) {
    uint32_t counter_ratio = 0;
    const int index = DrawIndex(unit_seed, "ratio", preset_ratios.size(),
                                &counter_ratio);
    values.device_pixel_ratio = preset_ratios[index];
    derived = true;
  } else if (preset_pixel_ratio > 0) {
    values.device_pixel_ratio = preset_pixel_ratio;
    derived = true;
  } else {
    values.device_pixel_ratio = 3.0;
    derived = true;
  }

  values.derived = derived;
  return values;
}

std::string UnitValuesSignature(const UnitValues& values) {
  std::string material;
  material += "cores=" + std::to_string(values.hardware_concurrency);
  material += "|mem=" + std::to_string(values.device_memory);
  material += "|touch=" + std::to_string(values.max_touch_points);
  material += "|screen=" + std::to_string(values.screen_width) + "x" +
              std::to_string(values.screen_height);
  material += "|dpr=" + std::to_string(values.device_pixel_ratio);

  uint8_t digest[SHA256_DIGEST_LENGTH];
  SHA256_CTX sha_ctx;
  SHA256_Init(&sha_ctx);
  SHA256_Update(&sha_ctx, material.data(), material.size());
  SHA256_Final(digest, &sha_ctx);

  static const char kHex[] = "0123456789abcdef";
  std::string out;
  out.reserve(sizeof(digest) * 2);
  for (size_t i = 0; i < sizeof(digest); ++i) {
    out.push_back(kHex[digest[i] >> 4]);
    out.push_back(kHex[digest[i] & 0x0f]);
  }
  return out;
}

}  // namespace nova
