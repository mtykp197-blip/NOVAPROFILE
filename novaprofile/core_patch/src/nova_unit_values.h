// NOVA Profile Core -- the per-unit values, derived in the core.
//
// The profile file carries the *family* of a device: the model, the
// graphics part, the panel, the browser major.  Those are shared by
// every unit of the family, exactly as they are shared by every handset
// of that model in a shop.
//
// The values that a site uses to tell two *people* apart are not in
// that family.  The core count, the amount of memory, the number of
// touch points, the panel's own resolution, the graphics limits --
// these are the readings that differ between two units that came out of
// the same factory, and they are what a script reads when it wants to
// know whether it is talking to a hundred thousand different handsets
// or to one handset wearing a hundred thousand hats.
//
// The core derives them from the profile's own seed, through the same
// HKDF the pixel noise uses.  Three properties follow:
//
//  1. **Determinism**: the same profile always reports the same core
//     count.  A profile that is closed and reopened is the same unit.
//
//  2. **Separation**: two profiles of the same model never report the
//     same core count, because the stratum and the seed are mixed in.
//
//  3. **Plausibility**: the derived value is drawn from the range the
//     *hardware* of that model could actually report.  A model whose
//     SoC has eight cores never reports sixteen, because a site that
//     checks the core count against the model would find the
//     contradiction.
//
// The values that the profile file states explicitly are authoritative:
// the core uses them when they are present and derives them only when
// the file leaves them out.  That way an operator who wants a fixed
// value can have one, and an operator who wants a fleet that separates
// itself can have that too.

#ifndef NOVA_PROFILE_CORE_UNIT_VALUES_H_
#define NOVA_PROFILE_CORE_UNIT_VALUES_H_

#include <cstdint>
#include <string>
#include <vector>

#include "nova_core_config.h"

namespace nova {

// The values that describe one unit within a family.
//
// The struct is filled in once, when the core is initialized, and is
// read from there after that.  Nothing is recomputed per frame.
struct UnitValues {
  // The logical core count the unit reports.
  int hardware_concurrency = 8;

  // The amount of memory in gigabytes, rounded the way the platform
  // rounds it (to a power of two, clamped to eight).
  int device_memory = 8;

  // The number of contacts the digitiser reports.
  int max_touch_points = 5;

  // The screen's own resolution, in the device's pixels.
  int screen_width = 0;
  int screen_height = 0;

  // The device pixel ratio.
  double device_pixel_ratio = 1.0;

  // The browser's build number within its major.
  int browser_build = 0;

  // The browser's patch level.
  int browser_patch = 0;

  // Whether the unit was derived from the seed rather than read from
  // the profile file.  The operator's file is never overwritten; this
  // only records what the core decided to report.
  bool derived = false;
};

// Derives the unit values of a profile.
//
// |config| is the profile's own configuration.  Where it states a
// value, that value is used; where it does not, the value is derived
// from the seed.
//
// |preset_cores|, when not empty, are the core counts the model's SoC
// could report.  The derived value is drawn from them, so the unit
// stays inside its family's range.
//
// |preset_memory|, when not empty, are the memory sizes the model could
// report, in gigabytes.
//
// |preset_touch|, when not empty, are the touch-point counts the
// model's digitiser could report.
//
// |preset_resolution| is the panel's own resolution.  A unit whose
// panel resolution is derived stays within a few pixels of the family's
// panel, because a handset of that model cannot have a different
// panel.
//
// |preset_ratios|, when not empty, are the device pixel ratios the
// model's panels are able to report.  The derived ratio is drawn from
// them, so the unit stays inside its family's range: a model whose
// panels are all 2.625 never reports 3.5.
UnitValues DeriveUnitValues(const ProfileConfig& config,
                             const std::vector<int>& preset_cores,
                             const std::vector<int>& preset_memory,
                             const std::vector<int>& preset_touch,
                             int preset_width,
                             int preset_height,
                             double preset_pixel_ratio,
                             const std::vector<double>& preset_ratios);

// A stable hash of the derived values.
//
// Two profiles that report the same unit values are the same unit to a
// site, whatever else they differ in.  The fleet builder uses this to
// check that a fleet does not collapse.
std::string UnitValuesSignature(const UnitValues& values);

}  // namespace nova

#endif  // NOVA_PROFILE_CORE_UNIT_VALUES_H_
