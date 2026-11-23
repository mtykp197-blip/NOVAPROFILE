// NOVA Profile Core -- the transport of the profile to the renderer.
//
// The browser process reads the profile from disk and the renderer has
// to have it.  The renderer cannot read the file itself: it is
// sandboxed and the user data directory is not among the paths it may
// open.  The browser is not sandboxed, so it reads the file and hands
// the document over.
//
// The hand-over rides on the renderer's command line.  A Windows
// command line is a single string that the system splits into
// arguments, and its quoting rules cannot describe a document that
// itself contains quotes, spaces and newlines: the renderer would
// receive a fragment of the JSON, fail to parse it, and fall back to
// the host's values -- which is the one thing the engine exists to
// prevent.
//
// Base64 fixes the quoting.  It has no spaces, no quotes and no
// newlines, so the argument survives the trip intact.
//
// It does not fix the length.  A Windows command line holds at most
// 32,767 characters, and the renderer is started with the profile *and*
// the JavaScript layer's payload on its command line.  Base64 of the
// two together is more than that.
//
// So both documents are deflated before they are encoded.  The profile
// is eighteen kilobytes of JSON and the payload is nine; deflated they
// are three and two, and base64 of that is seven in all.
//
// The marker at the front of the payload says whether it is
// compressed.  A reader that meets the marker inflates it; a reader
// that does not takes the payload as it stands.  That way a browser
// that was built before the compression was added still reads a payload
// that was written after it.

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_NOVA_NOVA_TRANSPORT_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_NOVA_NOVA_TRANSPORT_H_

#include <string>
#include <string_view>

namespace nova {

// The marker that says the payload is compressed.
//
// The payload starts with it, and a reader that finds it inflates what
// follows.  The marker holds a NUL as its first byte, which no JSON
// document starts with, so a plain document can never be mistaken for a
// compressed one.
extern const char kCompressedMarker[];

// Deflates ``contents`` for the command line.
//
// The result carries the marker, so the reader knows what to do with
// it.  An empty result means the compression failed, and the caller
// should then fall back to another transport.
std::string DeflateForCommandLine(std::string_view contents);

// The inverse of DeflateForCommandLine.
//
// ``encoded`` is the base64 of the deflated document.  It returns false
// when the argument is not valid base64.
bool InflateFromCommandLine(const std::string& encoded, std::string* out);

}  // namespace nova

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_NOVA_NOVA_TRANSPORT_H_
