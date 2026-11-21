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
bool LoadProfileConfigFromCommandLine(ProfileConfig* config, std::string* error);

}  // namespace nova

#endif  // NOVA_PROFILE_CORE_CONFIG_H_
