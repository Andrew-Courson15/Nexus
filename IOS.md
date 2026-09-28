# NEXUS for iPhone

There are two ways to have NEXUS on an iPhone.

1. **The web app on the home screen** (works today, no Mac needed): open your NEXUS address in
   Safari and choose **Share → Add to Home Screen**. See README → "NEXUS on your phone".
2. **The NEXUS iPhone app** (this folder, `ios/`): a real app. It opens NEXUS the same way, but
   it keeps NEXUS itself and your pictures on the phone, so it **always opens, even when the
   computer is off**, and it isn't subject to Safari clearing a home-screen app's storage.

Both talk to the NEXUS running on your computer, privately, through Tailscale. Your ideas stay
on your computer; the phone keeps a read-only copy for when the computer is off.

## How the app works

- The first time, it asks for your NEXUS address: scan the QR code NEXUS shows on the computer
  (**Settings → Your phone**) or type the address (like `https://gaming-pc.tail1234.ts.net`).
- NEXUS's screens are loaded from the computer and saved on the phone. When the computer can't
  be reached, the saved ones are used, with your saved copy of the archive.
- Picture thumbnails are saved on the phone as you see them (and when the copy is refreshed), so
  your pictures show with the computer off. Full-size pictures come from the computer.
- Captures made while the computer is off wait on the phone and are sent when it's back.
- **Settings → This device → Change NEXUS address** connects it to a different computer.
- Pull down to refresh. Press and hold the NEXUS icon on the Home Screen for **New idea** or
  **Search**.

Every push builds the app and runs it in the iPhone simulator against a real NEXUS: with the
computer on (a typed capture and a file go through), then off (it still opens and shows the
saved copy). Screenshots: GitHub → Actions → Build apps → the run → Artifacts →
`NEXUS-iphone-simulator-test`.

## Putting it on your own iPhone (free, needs a Mac)

1. On the Mac, install **Xcode** from the App Store (large; open it once to finish installing).
2. In Terminal:
   ```bash
   brew install xcodegen
   cd ~/nexus && git pull && cd ios && xcodegen
   open NEXUS.xcodeproj
   ```
3. In Xcode, click **NEXUS** (top of the left list) → **Signing & Capabilities**:
   - **Team:** choose your Apple ID (Xcode → Settings → Accounts → + if it isn't there).
   - **Bundle Identifier:** change `local.nexus.app` to something only yours, like
     `com.yourname.nexus`.
4. Plug the iPhone into the Mac, unlock it, and pick it at the top of the Xcode window.
5. Press ▶ (Run). The first time:
   - the iPhone asks you to turn on **Developer Mode** (Settings → Privacy & Security →
     Developer Mode → on, then restart);
   - then trust yourself as a developer: Settings → General → VPN & Device Management → your
     Apple ID → Trust.
6. NEXUS opens on the phone. Scan the QR code from NEXUS on the computer.

With a free Apple ID, apps you install this way stop opening after **7 days**; press ▶ again in
Xcode to renew. A paid Apple Developer account ($99/year) makes that a year, and is needed for
TestFlight and the App Store.

## Sebas (or anyone else)

The same app works with any NEXUS: each person scans the QR code of their own NEXUS. For others
to install it without Xcode, it has to go through TestFlight or the App Store (below).

## TestFlight and the App Store (for a public release)

What's needed, in order:

1. **An Apple Developer Program membership** ($99/year) in your name or a company's.
2. **A unique bundle identifier** (e.g. `com.yourname.nexus`) and an app record in
   App Store Connect with a name, icon (included), description, screenshots and a support URL.
3. **A privacy policy URL.** `PRIVACY.md` is the content; it has to be hosted on a web page.
   In the App Privacy form the answer is "Data Not Collected" (NEXUS sends nothing to you or
   anyone else; everything goes to the user's own computer).
4. **Archive and upload** from Xcode (Product → Archive → Distribute App). Then:
   - **TestFlight:** invite testers (like Sebas) by email; they install with the TestFlight app.
     No review for up to 100 people you invite directly ("internal testers"); external testers
     need a light review.
   - **App Store:** submit for review.
   Already done for you: the icon, Apple's privacy manifest (`PrivacyInfo.xcprivacy`: no tracking,
   no data collected), the encryption answer, and **screenshots**: every build makes App Store
   screenshots on the largest iPhone simulator with a made-up demo archive (Actions → Build apps →
   the run → Artifacts → `NEXUS-iphone-app-store-screenshots`). The app is iPhone-only (it also
   runs on an iPad, in iPhone size), so no iPad screenshots are needed.
5. **App Review things to expect**, and what to say:
   - *"The app needs a server."* Explain that NEXUS is a personal app for the user's own computer
     (like a remote for software they run), and give reviewers a demo NEXUS address and
     passcode that stays online during review (for example a small NEXUS on a cloud machine,
     reachable over HTTPS). Without one, review usually fails with "we couldn't use the app".
   - *Guideline 4.2 (minimum functionality) for web views:* this app adds real native work (it
     keeps NEXUS and pictures on the phone for offline use, sends captures later, scans the QR
     code, saves and shares files), which is the usual answer.
   - Encryption: the app only uses standard HTTPS, so export compliance is "no" (already set:
     `ITSAppUsesNonExemptEncryption = NO`).

## Still to do (native features that would make it better)

- Voice notes: the app's web view has the microphone and recorder NEXUS uses (checked in the
  simulator); recording on a real iPhone still needs trying.
- A share extension ("Share → NEXUS" from Photos or Notes). It needs an App Group shared with the
  app, which is simplest with a paid Apple Developer account, so it waits for that.
- Face ID for the passcode, and a widget for quick capture.
