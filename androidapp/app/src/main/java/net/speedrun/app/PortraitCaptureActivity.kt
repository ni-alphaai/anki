// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

import com.journeyapps.barcodescanner.CaptureActivity

/**
 * Portrait-locked QR capture screen. ZXing's default CaptureActivity opens in
 * sensor/landscape; declaring this subclass with `screenOrientation="portrait"`
 * in the manifest (and passing it via [com.journeyapps.barcodescanner.ScanOptions.setCaptureActivity])
 * makes the sync pairing scanner open upright.
 */
class PortraitCaptureActivity : CaptureActivity()
