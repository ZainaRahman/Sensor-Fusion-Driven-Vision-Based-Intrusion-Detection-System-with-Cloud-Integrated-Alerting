# Smart Pet & Security Alert System (Software Suite)

A distributed, real-time edge-to-cloud security software suite comprising an **Edge AI Computer Vision Service (Python)**, a **Serverless Cloud Layer (Firebase Firestore & FCM)**, and a **Cross-Platform Mobile Application (Flutter)**.

The software automates the detection of pets (cats/dogs) and humans, executes biometric facial comparison to distinguish authorized owners from unauthorized intruders, and streams structured alert records with compressed in-memory image thumbnails to the user's mobile device without incurring cloud storage costs.

---

## 🏗️ Software Architecture

```text
 ┌─────────────────────────────────────────────────────────────┐
 │                Edge AI Detection Engine (Python)            │
 │                                                             │
 │  Video Stream Input ──► YOLOv8 Multi-Frame Inference        │
 │                          │                                  │
 │        ┌─────────────────┴──────────────────┐               │
 │        ▼                                    ▼               │
 │  [Target: Cat / Dog]              [Target: Human]           │
 │        │                                    │               │
 │        │                       Face Recognition (dlib/RGB)  │
 │        │                       Compare with Owner Embedding │
 │        │                                    │               │
 │        │                      ┌─────────────┴─────────────┐ │
 │        │                      ▼                           ▼ │
 │        │               [Owner Matched]           [Stranger] │
 │        │                      │                           │ │
 │        ▼                      ▼                           ▼ │
 │  Alert Generation:    Alert Generation:           Alert:    │
 │  "Cat/Dog Detected"   "Authorized Human"     "Unauthorized"│
 │        │                      │                           │ │
 │        └──────────────────────┼───────────────────────────┘ │
 │                               ▼                             │
 │                  OpenCV In-Memory JPEG Resize               │
 │                  (320x240 @ 35% Quality -> Base64)          │
 └───────────────────────────────┬─────────────────────────────┘
                                 │
                                 ▼ (Firebase Admin SDK)
 ┌─────────────────────────────────────────────────────────────┐
 │               Serverless Cloud Backend (Firebase)           │
 │                                                             │
 │  • Cloud Firestore:                                         │
 │    - `detections/{id}`: Real-time alert logs & Base64 image │
 │    - `profile/owner`: Biometric facial template sync        │
 │  • Firebase Cloud Messaging (FCM):                          │
 │    - `farm_alerts` topic: Background/foreground push alerts │
 └───────────────────────────────┬─────────────────────────────┘
                                 │
                                 ▼ (Firestore Stream & FCM)
 ┌─────────────────────────────────────────────────────────────┐
 │                 Mobile Client (Flutter / Dart)              │
 │                                                             │
 │  • BLoC State Management: Reactive UI streams               │
 │  • In-Memory Image Rendering: Base64 to `Image.memory()`    │
 │  • Owner Profile Module: Photo capture, resize & cloud sync │
 │  • Deep Notification Routing: Direct alert document viewer  │
 └─────────────────────────────────────────────────────────────┘
```

---

## 💻 Software Components

### 1. Edge AI Computer Vision Engine (`raspberry_pi_alert.py`)
- **YOLOv8 Deep Learning Pipeline:** Employs an optimized YOLOv8 neural network to classify incoming camera frames into target classes (`Human`, `Cat`, `Dog`).
- **7-Frame Voting Algorithm:** Implements statistical temporal aggregation across 7 consecutive frames to eliminate transient false positives.
- **Biometric Facial Recognition:**
  - Extracts 128-dimensional facial embeddings using the `face_recognition` library (dlib).
  - Computes Euclidean distance against the owner's profile embedding using a strict similarity tolerance of `0.55`.
  - Classifies matched individuals as `Authorized Human Detected` and strangers as `Unauthorized Human Detected`.
- **Zero-Storage Image Pipeline:** Compresses the selected frame to a `320x240 px` JPEG with quality level 35 in memory and converts it directly into a Base64 string (~7–10 KB). This document-level storage eliminates the need for a paid cloud storage plan (compatible with the free Firebase Spark plan).
- **Dynamic Profile Listener:** Implements a persistent Firestore document listener on `profile/owner`. When the mobile user updates their profile image, the Python service fetches the image, recomputes the face embedding, and updates its local cache without restarting.

### 2. Cloud Infrastructure (Firebase)
- **Cloud Firestore:**
  - `detections` collection: Stores alert metadata (`title`, `type`, `date`, `time`, `timestamp`, `confidence`, `severity`, `cameraName`, `imageBase64`).
  - `profile` collection: Stores owner metadata and base64 reference face template (`imageBase64`, `name`, `updatedAt`).
- **Firebase Cloud Messaging (FCM):** Dispatches push notifications to devices subscribed to the `farm_alerts` topic for real-time mobile alerting.
- **Security Rules:** Granular access rules enabling authenticated/open read-write operations for active detection and profile synchronization.

### 3. Mobile Client Application (Flutter)
- **Architecture & State Management:** Implements the BLoC (Business Logic Component) pattern for predictable state transitions and separation of concerns.
- **Service Locator:** Powered by `get_it` for dependency injection across repositories, services, and blocs.
- **Real-Time Stream Synchronization:** Subscribes to Firestore snapshot listeners for reactive, zero-latency dashboard updates without requiring manual pull-to-refresh.
- **In-Memory Image Decoding:** Decodes Base64 payloads directly via `dart:convert` and renders them with `Image.memory()`.
- **Intelligent Notification Routing:** When an FCM push notification is tapped, the app extracts the `doc_id`, fetches the full document from Firestore, and navigates directly to the alert details view with the image preloaded.
- **Owner Profile Management:** Integrates `image_picker` with automatic native downscaling (`320x320 px`, 35% quality) to upload optimized biometric templates to Firestore.

---

## 🛠️ Tech Stack & Dependencies

### Flutter Mobile Application
| Dependency | Purpose |
|---|---|
| `flutter_bloc` (`^8.1.3`) | Reactive state management for UI and alerts |
| `cloud_firestore` (`^4.15.8`) | Real-time database streams and document manipulation |
| `firebase_messaging` (`^14.7.19`) | Background and foreground push notifications |
| `flutter_local_notifications` (`^17.0.0`) | Heads-up foreground notification display |
| `go_router` (`^13.2.0`) | Declarative application routing |
| `image_picker` (`^1.2.3`) | Profile picture capture and selection with compression |
| `get_it` (`^7.6.0`) | Dependency injection and service locator |
| `google_fonts` (`^6.1.0`) | Modern Poppins typography system |


---

## ⚙️ Installation & Deployment

### Step 1: Cloud Backend Setup (Firebase)
1. In the [Firebase Console](https://console.firebase.google.com/), create a project named `fisheries-farm-detection`.
2. Enable **Cloud Firestore** and **Firebase Cloud Messaging**.
3. Under **Firestore Database > Rules**, deploy the following security rules:
   ```javascript
   rules_version = '2';
   service cloud.firestore {
     match /databases/{database}/documents {
       match /detections/{id} {
         allow read, write: if true;
       }
       match /profile/{id} {
         allow read, write: if true;
       }
     }
   }
   ```
4. In **Project Settings > Service Accounts**, click **Generate new private key** and save it as `serviceAccountKey.json` in the root directory.
5. In **Project Settings > General > Your Apps**, register an Android app (package: `com.example.detect_pet_unauthorized_person_with_image_processing_mobile_interface_for_owner`), download `google-services.json`, and place it in `android/app/`.

---

### Step 2: Python Edge Service Setup
1. Clone or copy the project files to the target machine/device.
2. Install the required system libraries and Python packages:
   ```bash
   pip3 install firebase-admin ultralytics opencv-python requests numpy face_recognition
   ```
3. Confirm that `serviceAccountKey.json` is in the same directory as `raspberry_pi_alert.py`.
4. Configure your camera stream URL inside `raspberry_pi_alert.py`:
   ```python
   LAPTOP_STREAM_URL = "http://<CAMERA_STREAM_IP>:5001/frame"
   ```
5. Execute the edge detection service:
   ```bash
   python3 raspberry_pi_alert.py
   ```

---

### Step 3: Flutter Mobile Application Setup
1. Ensure the Flutter SDK (`>=3.11.4`) is installed on your development machine.
2. Fetch the dependencies:
   ```bash
   flutter pub get
   ```
3. Run the static analyzer to confirm clean code health:
   ```bash
   flutter analyze
   ```
4. Build the production release APK:
   ```bash
   flutter build apk --release
   ```
   *Generated output:* `build/app/outputs/flutter-apk/app-release.apk`
5. Install the APK directly on your Android device:
   ```bash
   flutter install
   ```

---

## 🔄 Software Data Flow & Business Logic

1. **Owner Registration:**
   - The user opens the **Owner Profile** screen in the mobile app.
   - The user captures/selects a facial photograph.
   - The app downscales the image to a lightweight Base64 string and writes it to Firestore (`profile/owner`).
   - The Python script's background Firestore listener detects the update, computes the 128-d face embedding, and caches it in memory.

2. **Detection & Verification:**
   - The edge detection engine identifies an object across multiple frames using YOLOv8.
   - If an animal (`Cat` or `Dog`) is detected, it logs the event with its confidence score and Base64 frame.
   - If a `Human` is detected, the engine passes the bounding crop to `face_recognition`:
     - **Match found:** Title is set to **`Authorized Human Detected`**, type is **`human`**, and buzzer triggers are suppressed.
     - **No match:** Title is set to **`Unauthorized Human Detected`**, type is **`human`**, and an intruder alert sequence is initiated.

3. **Notification & Inspection:**
   - The detection document is created in Firestore and an FCM push notification is published.
   - The mobile application's real-time stream displays the new alert card with visual status badges (Green for Authorized, Red for Unauthorized, Orange/Blue for Pets).
   - Tapping an alert or its push notification opens the **Alert Details** screen, decoding the Base64 thumbnail in memory alongside detection metadata.

---

## 📄 License

This software project is licensed under the [MIT License](LICENSE).
