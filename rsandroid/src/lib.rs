// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

//! Minimal JNI bridge from the Android shell to the shared Anki/Speedrun Rust
//! engine (`anki::backend::Backend`).
//!
//! The Android app talks to exactly the same protobuf service boundary that the
//! desktop app uses: `Backend::run_service_method(service, method, input)`. This
//! is the "shared engine" requirement - the same Rust core (including the
//! Speedrun diagnostic engine and points-at-stake queue) runs on both desktop
//! and phone.
//!
//! Memory model: `openBackend` boxes a `Backend` and returns the raw pointer as
//! a `jlong` handle; `runMethod` borrows it; `closeBackend` frees it. The
//! caller must not use the handle after closing.
//!
//! NOTE (Phase 1E): authored but not yet compiled here (no Android NDK).

use anki::backend::init_backend;
use anki::backend::Backend;
use jni::objects::JByteArray;
use jni::objects::JClass;
use jni::sys::jbyteArray;
use jni::sys::jint;
use jni::sys::jlong;
use jni::JNIEnv;

/// Open a backend from an encoded `anki.backend.BackendInit` message. Returns a
/// handle, or 0 on failure.
#[no_mangle]
pub extern "system" fn Java_net_speedrun_app_NativeBackend_openBackend<'local>(
    env: JNIEnv<'local>,
    _class: JClass<'local>,
    init_msg: JByteArray<'local>,
) -> jlong {
    let bytes = env.convert_byte_array(&init_msg).unwrap_or_default();
    match init_backend(&bytes) {
        Ok(backend) => Box::into_raw(Box::new(backend)) as jlong,
        Err(_) => 0,
    }
}

/// Run a protobuf service method. `service` and `method` are the generated
/// indices (the same ones the desktop/Python clients use); `input` is the
/// encoded request.
///
/// The returned byte array is status-tagged: the first byte is `1` for success
/// (followed by the encoded response) or `0` for failure (followed by an
/// encoded `anki.backend.BackendError`). This lets the Kotlin side surface real
/// engine errors instead of silently misparsing an error as an empty response.
#[no_mangle]
pub extern "system" fn Java_net_speedrun_app_NativeBackend_runMethod<'local>(
    env: JNIEnv<'local>,
    _class: JClass<'local>,
    ptr: jlong,
    service: jint,
    method: jint,
    input: JByteArray<'local>,
) -> jbyteArray {
    if ptr == 0 {
        return std::ptr::null_mut();
    }
    let backend = unsafe { &*(ptr as *const Backend) };
    let bytes = env.convert_byte_array(&input).unwrap_or_default();
    let (tag, out) = match backend.run_service_method(service as u32, method as u32, &bytes) {
        Ok(v) => (1u8, v),
        Err(v) => (0u8, v),
    };
    let mut tagged = Vec::with_capacity(out.len() + 1);
    tagged.push(tag);
    tagged.extend_from_slice(&out);
    env.byte_array_from_slice(&tagged)
        .map(|arr| arr.into_raw())
        .unwrap_or(std::ptr::null_mut())
}

/// Free a backend handle previously returned by `openBackend`.
#[no_mangle]
pub extern "system" fn Java_net_speedrun_app_NativeBackend_closeBackend(
    _env: JNIEnv,
    _class: JClass,
    ptr: jlong,
) {
    if ptr != 0 {
        unsafe {
            drop(Box::from_raw(ptr as *mut Backend));
        }
    }
}
