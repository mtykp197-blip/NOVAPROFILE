// NOVA Profile Core -- the browser-side fingerprint engine.
//
// This file defines the configuration structures that the control plane
// hands to the browser process, together with the deterministic derivation
// logic that mirrors the Python side exactly.
//
// The design rule is simple and absolute: **every** value that reaches the
// renderer must come from the configuration, never from a JavaScript hook.
// A JS hook is trivially detectable (Function.prototype.toString,
// Object.getOwnPropertyDescriptor, Proxy traps), so the injection happens
// below Blink, inside the C++ process, before the renderer is created.
//
// The config arrives as JSON on the command line:
//
//     --nova-config=/path/to/nova_core.json
//
// and is parsed by the browser process before any child process (renderer,
// GPU, network service) is forked.

#ifndef NOVA_PROFILE_CORE_CONFIG_H_
#define NOVA_PROFILE_CORE_CONFIG_H_

#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

#include "base/command_line.h"

namespace nova {

// ---------------------------------------------------------------------------
// Identity and seed
// ---------------------------------------------------------------------------

// The root of the fingerprint. Everything else is derived from it.
struct SeedConfig {
  // 64-character hex string.
  std::string master_seed;
  uint32_t seed_epoch = 0;
  uint32_t nudge_step = 0;
  std::string hkdf_version;
  std::string preset_id;
  std::string core_version;

  // Which unit of the device family this profile is.
  //
  // The narrow readings -- the browser major, the core count, the
  // memory -- are drawn *stratified*, so that two handsets of one model
  // do not report the same ones.  The stratum is the unit's place in
  // the family, and it has to travel with the seed: a profile that is
  // regenerated from its seed must come back as the same handset, or
  // its fingerprint changes every time the operator opens it.
  //
  // The value is optional.  A profile that stands alone draws every
  // choice independently and the stratum is then unused.
  //
  // The value is stored as a signed number so that "absent" and "zero"
  // are different things: a stratum of zero is a real unit -- the first
  // one of the family -- and it is not the same as a profile that does
  // not belong to a family at all.
  bool has_stratum = false;
  uint32_t stratum = 0;
};

// ---------------------------------------------------------------------------
// Android device
// ---------------------------------------------------------------------------

struct AndroidBuild {
  std::string version_release;
  int sdk_int = 0;
  std::string security_patch;
  std::string build_id;
  std::string display;
  std::string version_incremental;
  std::string model;
  std::string device;
  std::string product;
  std::string manufacturer;
  std::string brand;
  std::string hardware;
  std::string board;
  std::string bootloader;
  std::string fingerprint;
  std::string host;
  std::string user;
  std::string tags;
  std::string type;
  std::string serial;
};

struct AndroidAbiSoc {
  std::vector<std::string> supported_abis;
  std::vector<std::string> supported_64_bit_abis;
  std::vector<std::string> supported_32_bit_abis;
  std::string primary_abi;
  bool is_64_bit = true;
  std::string soc_vendor;
  std::string soc_model;
  std::string cpu_cluster_layout;
  std::string gpu_architecture;
};

// ---------------------------------------------------------------------------
// Screen and viewport
// ---------------------------------------------------------------------------

struct Screen {
  int width = 0;
  int height = 0;
  int avail_width = 0;
  int avail_height = 0;
  int avail_left = 0;
  int avail_top = 0;
  int color_depth = 24;
  int pixel_depth = 24;
  double device_pixel_ratio = 1.0;
  int density_dpi = 0;

  // The ratios the *panels* of this model are able to report.
  //
  // A panel reports the ratio of its density bucket, and a model is
  // offered a handful of buckets, so the set is short and closed.  When
  // the ratio above is left at zero, the core draws one from this list
  // instead -- from the profile's own seed, so two units of the same
  // model report different ratios and neither of them reports a ratio
  // its panels cannot produce.
  std::vector<double> device_pixel_ratio_options;
};

struct Orientation {
  std::string type;
  int angle = 0;
  bool lock_supported = true;
};

struct SafeAreaInsets {
  int top = 0;
  int right = 0;
  int bottom = 0;
  int left = 0;
};

struct Viewport {
  int inner_width = 0;
  int inner_height = 0;
  int outer_width = 0;
  int outer_height = 0;
  int screen_x = 0;
  int screen_y = 0;
  double visual_viewport_scale = 1.0;
  SafeAreaInsets safe_area_insets;
  int scrollbar_width = 0;
};

struct MediaQueries {
  std::string prefers_color_scheme = "light";
  std::string prefers_reduced_motion = "no-preference";
  std::string prefers_contrast = "no-preference";
  std::string forced_colors = "none";
  std::string pointer = "coarse";
  std::vector<std::string> any_pointer{"coarse"};
  std::string hover = "none";
  std::vector<std::string> any_hover{"none"};
  std::string color_gamut = "srgb";
  std::string dynamic_range = "standard";
};

// ---------------------------------------------------------------------------
// Canvas, WebGL and WebGPU
// ---------------------------------------------------------------------------

struct Canvas2DConfig {
  std::string mode = "noise";
  std::string salt;
  double noise_strength = 0.0;
  bool persist_across_restarts = true;
  std::vector<std::string> targets;
};

struct WebGLImageNoise {
  bool enabled = true;
  // A decimal string with at least 17 significant digits.  It is parsed
  // into a double without any intermediate rounding.
  std::string value;
};

struct WebGLParameters {
  int max_texture_size = 0;
  int max_cube_map_texture_size = 0;
  int max_3d_texture_size = 0;
  int max_array_texture_layers = 0;
  int max_renderbuffer_size = 0;
  std::vector<int> max_viewport_dims;
  int max_vertex_attribs = 0;
  int max_vertex_uniform_vectors = 0;
  int max_vertex_uniform_components = 0;
  int max_fragment_uniform_vectors = 0;
  int max_fragment_uniform_components = 0;
  int max_varying_vectors = 0;
  int max_combined_texture_image_units = 0;
  int max_vertex_texture_image_units = 0;
  int max_texture_image_units = 0;
  int max_color_attachments = 0;
  int max_draw_buffers = 0;
  int max_samples = 0;
  int max_uniform_buffer_bindings = 0;
  std::vector<double> aliased_line_width_range;
  std::vector<double> aliased_point_size_range;
  int sample_buffers = 0;
  int samples = 0;
  int max_texture_max_anisotropy_ext = 0;
};

struct ShaderPrecisionRange {
  int range_min = 0;
  int range_max = 0;
  int precision = 0;
};

struct ShaderPrecision {
  std::map<std::string, ShaderPrecisionRange> vertex_shader;
  std::map<std::string, ShaderPrecisionRange> fragment_shader;
};

struct WebGLConfig {
  std::string mode = "noise";
  std::string unmasked_vendor;
  std::string unmasked_renderer;
  std::string vendor;
  std::string renderer;
  std::string version;
  std::string shading_language_version;
  WebGLImageNoise image_noise;
  // The per-unit salt of the draw surface.  The pixel-noise value carries
  // only about 56 bits, which at 100 million profiles collides with a
  // probability of roughly 6.7 percent; the salt is what the readback is
  // keyed on, and it is 128 bits wide.
  std::string salt;
  bool read_pixels_sync = true;
  std::vector<std::string> extensions;
  WebGLParameters parameters;
  ShaderPrecision shader_precision;
};

struct WebGPUAdapter {
  std::string vendor;
  std::string architecture;
  std::string device;
  std::string description;
  bool is_fallback_adapter = false;
};

struct WebGPUConfig {
  bool enabled = false;
  WebGPUAdapter adapter;
  std::vector<std::string> features;
  std::map<std::string, double> limits;
  // The per-device salt.  The adapter and the limit table belong to the
  // GPU *model* and are shared by a whole family of units, exactly as on
  // real hardware; the salt identifies the individual unit within it.
  std::string salt;
};

// ---------------------------------------------------------------------------
// Signals
// ---------------------------------------------------------------------------

struct AudioConfig {
  std::string mode = "native_quantized";
  std::string salt;
  int sample_rate = 48000;
  double base_latency = 0.0;
  double output_latency = 0.0;
  int channel_count = 2;
  int max_channel_count = 2;
  bool respect_core_quantization = true;
  // The magnitude of the perturbation that is applied to the rendered
  // samples.  Zero leaves the samples untouched, which is the right
  // thing for a profile that only wants the sample rate and the
  // latencies to be spoofed.
  double noise_strength = 0.0;
};

struct RectsConfig {
  std::string mode = "noise";
  std::string salt;
  int delta_x = 0;
  int delta_y = 0;
  int delta_width = 0;
  int delta_height = 0;
  std::vector<std::string> targets;
};

struct FontsConfig {
  std::string mode = "android_preset";
  std::vector<std::string> installed;
  std::string salt;
  bool preserve_metric_clusters = true;
};

struct MediaDeviceEntry {
  std::string device_id;
  std::string group_id;
  std::string label;
  std::string facing_mode;
};

struct MediaDevicesConfig {
  std::string mode = "deterministic_mock";
  std::string id_scope = "per_profile_and_origin";
  std::string salt;
  std::vector<MediaDeviceEntry> audio_inputs;
  std::vector<MediaDeviceEntry> audio_outputs;
  std::vector<MediaDeviceEntry> video_inputs;
};

struct StorageNetworkConfig {
  uint64_t quota_bytes = 0;
  uint64_t usage_bytes = 0;
  bool persisted_default = false;
  std::string effective_type = "4g";
  int rtt = 0;
  double downlink = 0.0;
  bool save_data = false;
  std::string connection_type = "wifi";
  // The per-unit salt of the storage surface.  The quota is one of a
  // handful of round numbers and the usage is a fraction of it, so
  // without the salt the surface carries barely a dozen bits.
  std::string salt;
};

struct BatteryConfig {
  bool enabled = true;
  bool charging = false;
  double level = 1.0;
  int charging_time = 0;
  int discharging_time = 0;
};

struct SpeechVoice {
  std::string name;
  std::string lang;
  bool is_default = false;
  bool local_service = true;
  std::string voice_uri;
};

struct GamepadConfig {
  bool enabled = false;
};

struct BatterySpeechGamepad {
  BatteryConfig battery;
  std::vector<SpeechVoice> speech_voices;
  GamepadConfig gamepad;
};

struct Vector3D {
  double x = 0.0;
  double y = 0.0;
  double z = 0.0;
};

struct Orientation3D {
  double alpha = 0.0;
  double beta = 0.0;
  double gamma = 0.0;
};

struct SensorsConfig {
  bool enabled = true;
  int sampling_hz = 60;
  std::string motion_profile = "stationary_micro_jitter";
  Vector3D accelerometer;
  Vector3D gyroscope;
  Vector3D linear_acceleration;
  Vector3D gravity;
  Orientation3D absolute_orientation;
  Orientation3D relative_orientation;
  // The per-unit salt of the motion surface.  The gravity reading is
  // about 9.81 on every device on Earth, so the salt is what makes
  // the surface able to tell a hundred million units apart.
  std::string salt;
};

// ---------------------------------------------------------------------------
// Navigator, client hints and permissions
// ---------------------------------------------------------------------------

struct BrandVersion {
  std::string brand;
  std::string version;
};

struct NavigatorConfig {
  std::string user_agent;
  std::string app_version;
  std::string app_name;
  std::string app_code_name;
  std::string vendor;
  std::string vendor_sub;
  std::string product;
  std::string product_sub;
  std::string platform;
  int hardware_concurrency = 8;
  int device_memory = 8;
  int max_touch_points = 5;
  std::string language;
  std::vector<std::string> languages;
  bool cookie_enabled = true;
  bool pdf_viewer_enabled = true;
  bool webdriver = false;
  bool on_line = true;
  bool java_enabled = false;

  // The values the *hardware* of this model is able to report.
  //
  // These are not the values the profile reports; they are the range the
  // model could report.  A model whose SoC has eight cores can never
  // report sixteen, and a model whose digitiser takes five fingers can
  // never report ten.  When a value above is left at zero, the core
  // draws one from these lists instead -- and draws it from the
  // profile's own seed, so that two units of the same model report
  // different values and neither of them reports an impossible one.
  std::vector<int> hardware_concurrency_options;
  std::vector<int> device_memory_options;
  std::vector<int> max_touch_points_options;
};

struct UserAgentDataConfig {
  std::vector<BrandVersion> brands;
  std::vector<BrandVersion> full_version_list;
  bool mobile = true;
  std::string platform = "Android";
  std::string platform_version;
  std::string architecture;
  std::string bitness;
  std::string model;
  bool wow64 = false;

  // The full version of the browser, as the high-entropy hint
  // ``uaFullVersion`` carries it.
  //
  // The hint is the build number of the browser, and it is a value of
  // its own: the version list carries it per brand, and the hint
  // carries the one of the real brand.  Without it the hint is empty,
  // and a page that reads the hint and the list sees a browser that
  // knows its brands but not its own version.
  std::string ua_full_version;
};

struct PermissionsConfig {
  std::map<std::string, std::string> policies;
};

// ---------------------------------------------------------------------------
// Timezone, locale and geolocation
// ---------------------------------------------------------------------------

struct TimezoneConfig {
  std::string iana_name;
  int offset_minutes = 0;
  bool dst_active = false;
  std::string abbreviation;
};

struct LocaleConfig {
  std::string primary;
  std::string intl_locale;
  std::string numbering_system = "latn";
  std::string calendar = "gregory";
  std::string hour_cycle = "h23";
  std::string currency;
};

struct GeolocationConfig {
  std::string mode = "prompt";
  std::optional<double> latitude;
  std::optional<double> longitude;
  std::optional<double> accuracy;
  std::optional<double> altitude;
  std::optional<double> altitude_accuracy;
  std::optional<double> heading;
  std::optional<double> speed;
  int timestamp_jitter_ms = 0;
};

// ---------------------------------------------------------------------------
// Network
// ---------------------------------------------------------------------------

struct ProxyConfig {
  bool enabled = false;
  std::string protocol = "direct";
  std::string host;
  int port = 0;
  std::string username;
  std::string password;
  std::string auth_method = "none";
  int connect_timeout_ms = 30000;
  int read_timeout_ms = 60000;
};

struct WebRtcConfig {
  std::string mode = "proxy_only";
  std::string spoofed_public_ip_v4;
  std::string spoofed_public_ip_v6;
  std::string spoofed_local_ip_v4;
  bool mdns_enabled = true;
  bool block_non_proxied_udp = true;
  std::string ice_transport_policy = "relay";
};

struct DnsConfig {
  std::string mode = "proxy_remote";
  std::vector<std::string> servers;
  std::string doh_url;
  std::string dot_host;
  bool prevent_dns_leak = true;
};

// The TLS handshake.
//
// JA3 is the MD5 of the ClientHello -- the cipher suites, the extension
// order, the elliptic curves and the elliptic curve point formats, in the
// order the client sent them.  JA4 is a readable form of the same thing.
//
// The handshake happens in the network service, before any script runs,
// so no amount of JavaScript can change it.  The only way to change it
// is to change the bytes the client puts on the wire, which means
// changing the network stack.
//
// The order of the cipher suites and of the extensions is what a
// fingerprint is made of, so both are lists here rather than sets.
struct TlsConfig {
  std::string mode = "native";
  // The cipher suites, in the order they are offered.
  std::vector<std::string> cipher_suites;
  // The supported groups, in the order they are offered.
  std::vector<std::string> supported_groups;
  // The signature algorithms, in the order they are offered.
  std::vector<std::string> signature_algorithms;
  // The ALPN protocols, in the order they are offered.
  std::vector<std::string> alpn_protocols;
  // The TLS versions, from the oldest to the newest.
  std::vector<std::string> versions;
  // The extension order, by the name that BoringSSL uses for the
  // extension.  An extension that is not listed keeps the position that
  // BoringSSL would have given it.
  std::vector<std::string> extension_order;
  // The per-profile salt.  The handshake is the same for every profile
  // that uses the same browser build unless something in the profile
  // varies it, so the salt is what makes the handshake of two profiles
  // differ.
  std::string salt;
  // Whether the GREASE extension is sent.
  bool grease_enabled = true;
};

struct HttpHeadersConfig {
  std::string accept_language;
  std::string dnt;
  std::string sec_ch_ua;
  std::string sec_ch_ua_mobile = "?1";
  std::string sec_ch_ua_platform = "\"Android\"";
  std::string sec_ch_ua_platform_version;
  std::string sec_ch_ua_arch;
  std::string sec_ch_ua_bitness;
  std::string sec_ch_ua_model;
  std::string sec_ch_ua_full_version_list;
  std::map<std::string, std::string> custom_headers;
};

struct GeoIntelligenceConfig {
  std::string exit_ip_v4;
  std::string exit_ip_v6;
  std::string country_code;
  std::string country_name;
  std::string region_code;
  std::string region_name;
  std::string city;
  std::string postal_code;
  int asn = 0;
  std::string isp;
  std::string org;
  bool is_mobile_carrier = false;
  bool is_datacenter = false;
  int latency_ms = 0;
};

struct GeoSyncConfig {
  bool enabled = true;
  bool lock_manual_overrides = true;
  bool sync_timezone = true;
  bool sync_locale = true;
  bool sync_geolocation = true;
  bool sync_webrtc_ip = true;
};

// ---------------------------------------------------------------------------
// The aggregate configuration
// ---------------------------------------------------------------------------

struct ProfileConfig {
  std::string profile_id;
  SeedConfig seed_config;
  AndroidBuild android_build;
  AndroidAbiSoc android_abi_soc;
  NavigatorConfig navigator;
  UserAgentDataConfig user_agent_data;
  PermissionsConfig permissions;
  Screen screen;
  Orientation orientation;
  Viewport viewport;
  MediaQueries media_queries;
  TimezoneConfig timezone;
  LocaleConfig locale;
  GeolocationConfig geolocation;
  Canvas2DConfig canvas_2d;
  WebGLConfig webgl;
  WebGPUConfig webgpu;
  AudioConfig audio;
  RectsConfig rects;
  FontsConfig fonts;
  MediaDevicesConfig media_devices;
  StorageNetworkConfig storage_network;
  BatterySpeechGamepad battery_speech_gamepad;
  SensorsConfig sensors;
  ProxyConfig proxy;
  GeoSyncConfig geo_sync;
  GeoIntelligenceConfig geo_intelligence;
  WebRtcConfig webrtc;
  DnsConfig dns;
  TlsConfig tls;
  HttpHeadersConfig http_headers;
};

// Parses the JSON document produced by the control plane.
//
// Returns true on success.  On failure the function writes a human readable
// diagnostic into |error| and leaves |config| untouched.
bool ParseProfileConfig(const std::string& json, ProfileConfig* config,
                        std::string* error);

// Reads the configuration that the browser process was launched with.
//
// The browser process reads ``--nova-config=<path>`` from its command line.
// The path is validated before the file is opened so that a malformed
// switch cannot be used to read an arbitrary file.
//
// The renderer is sandboxed and cannot open the file, so the browser
// process reads it and hands the document to the renderer on the
// command line.  The renderer prefers that document and falls back to
// the path.
bool LoadProfileConfigFromCommandLine(ProfileConfig* config, std::string* error);

// The name of the switch that carries the configuration to the renderer.
extern const char kConfigSourceSwitchName[];

// Hands the browser process its own copy of the profile.
//
// The browser process is given a path, and it is the only process that
// is not sandboxed, so it is the one that reads the file.  The startup
// code reads it and hands the document here, and the hooks pick it up
// from this side.
//
// The document is the raw JSON.  It is handed over rather than read
// again because the read may only happen on the main thread during
// startup, and the hooks are reached from threads that are not allowed
// to wait on the disk.
void SetBrowserConfigDocument(const std::string& document);

// The browser process's copy of the profile.
//
// An empty result means there is none: the browser was not started
// with a profile it could read.
std::string ReadBrowserConfigDocument();

}  // namespace nova

#endif  // NOVA_PROFILE_CORE_CONFIG_H_
