import warnings
warnings.filterwarnings("ignore")
import cv2
import time
import datetime
import os
import threading
from ultralytics import YOLO
from collections import defaultdict, deque

# Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials, firestore, storage, messaging

# GPIO
from gpiozero import DigitalInputDevice, DigitalOutputDevice

# Serial (Arduino ultrasonic node)
import serial

# Camera (Arducam IMX519 via CSI)
from picamera2 import Picamera2


import board
import busio
from PIL import Image, ImageDraw, ImageFont
import adafruit_ssd1306

SERVICE_ACCOUNT_KEY_PATH = "serviceAccountKey.json"
FIREBASE_STORAGE_BUCKET = "fisheries-farm-detection.firebasestorage.app"

DEVICE_NAME = "Raspberry Pi 5"
CAMERA_NAME = "Front Gate Camera"
LOCATION    = "Fisheries Farm - Front Gate"

FRAMES_TO_CAPTURE = 7
MIN_CONFIDENCE    = 0.5
SAVE_DIR          = "detections"
WINDOW_NAME       = "Fisheries Farm - Live Camera"

FIRESTORE_COLLECTION = "detections"

TARGET_CLASSES = {
    0:  "Human",
    15: "Cat",
    16: "Dog",
}

CLASS_COLORS = {
    "Human": (0,   0,   255),
    "Owner": (255, 255,   0),
    "Cat":   (0,   165, 255),
    "Dog":   (0,   255,   0),
}

# --- Arduino serial config ---
SERIAL_PORT      = "/dev/ttyACM0"   
SERIAL_BAUD      = 9600
SERIAL_TIMEOUT_S = 1.0
US_OUT_OF_RANGE  = 999               
ULTRASONIC_GATE_CM = 25
US_HISTORY_LEN       = 5
US_CONSENSUS_REQUIRED = 3

# --- Arducam IMX519 camera config ---
CAMERA_RESOLUTION = (1280, 720)                          
CAMERA_WARMUP_S   = 2.0            

# Global variables for Owner face recognition
OWNER_ENCODING = None
OWNER_NAME = "Owner"


US1_DIST = US_OUT_OF_RANGE
US2_DIST = US_OUT_OF_RANGE
US_LOCK  = threading.Lock()
US_LAST_UPDATE = 0.0
ARDUINO_CONNECTED = False

# --- rolling history of recent raw readings, used for consensus filtering ---
US1_HISTORY = deque(maxlen=US_HISTORY_LEN)
US2_HISTORY = deque(maxlen=US_HISTORY_LEN)

# --- camera handle, set by init_camera() ---
picam2 = None
oled = None

# =============================================================================
#  OLED DISPLAY FUNCTIONS
# =============================================================================
def init_oled():
    global oled
    try:
        i2c = board.I2C()
        oled = adafruit_ssd1306.SSD1306_I2C(128, 64, i2c, addr=0x3c)
        update_oled_display("System Ready", "Monitoring...")
        print("[OLED] Display initialized successfully on 0x3C.")
    except Exception as e:
        print(f"[OLED] ERROR initializing display: {e}")

def update_oled_display(line1, line2=""):
    global oled
    if oled is None:
        print("[OLED] Warning: Display object is None. Cannot print text.")
        return
    try:
        # Create image buffer matching the 128x64 display size
        image = Image.new("1", (128, 64))
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        
        # Render text lines
        draw.text((0, 4),  f"STATUS:", font=font, fill=255)
        draw.text((0, 20), f"> {line1}", font=font, fill=255)
        if line2:
            draw.text((0, 40), f"> {line2}", font=font, fill=255)
            
        oled.fill(0)
        oled.image(image)
        oled.show()
    except Exception as e:
        print(f"[OLED] Write error: {e}")

# =============================================================================
#  FIREBASE INITIALISATION  
# =============================================================================
def init_firebase():
    try:
        cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
        firebase_admin.initialize_app(cred, {
            'storageBucket': FIREBASE_STORAGE_BUCKET
        })
        print("[Firebase] Initialised successfully")
        return True
    except Exception as e:
        print(f"[Firebase] ERROR initialising Firebase: {e}")
        print("[Firebase] Detections will be saved locally only.")
        return False


def check_storage_available():
    try:
        bucket = storage.bucket()
        bucket.exists()
        print("[Storage] Bucket accessible - image upload enabled")
        return True
    except Exception as e:
        print(f"[Storage] NOT available (likely Spark plan) - image upload disabled: {e}")
        return False


def compute_face_encoding_from_base64(base64_str):
    """
    Decodes a base64 string, converts to image, and extracts face encoding.
    Requires face_recognition library.
    """
    try:
        import base64
        import numpy as np
        import cv2
        try:
            import face_recognition
        except ImportError:
            print("[FaceRec] WARNING: 'face_recognition' library is not installed.")
            print("[FaceRec] To enable owner face recognition, run: pip install face_recognition")
            return None
        img_bytes = base64.b64decode(base64_str)
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return None

        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        encodings = face_recognition.face_encodings(rgb_img)
        if encodings:
            return encodings[0]
        else:
            print("[FaceRec] No faces found in profile image.")
            return None
    except Exception as e:
        print(f"[FaceRec] Error computing face encoding: {e}")
        return None


def start_profile_listener():
    """
    Starts a real-time Firestore listener for owner profile changes.
    Decodes the new owner base64 image and computes face encoding.
    """
    try:
        db = firestore.client()
        doc_ref = db.collection("profile").document("owner")

        def on_snapshot(doc_snapshot, changes, read_time):
            global OWNER_ENCODING, OWNER_NAME
            for doc in doc_snapshot:
                if doc.exists:
                    data = doc.to_dict()
                    OWNER_NAME = data.get("name", "Owner")
                    b64_str = data.get("imageBase64", "")
                    if b64_str:
                        print(f"\n[ProfileListener] New profile image detected for {OWNER_NAME}. Computing face encoding...")
                        encoding = compute_face_encoding_from_base64(b64_str)
                        if encoding is not None:
                            OWNER_ENCODING = encoding
                            print("[ProfileListener] Face encoding computed successfully and updated.\n")
                        else:
                            print("[ProfileListener] Failed to compute face encoding from new image.\n")
                    else:
                        print("[ProfileListener] Profile document exists but imageBase64 is empty.\n")
                        OWNER_ENCODING = None
                else:
                    print("[ProfileListener] Profile document does not exist.\n")
                    OWNER_ENCODING = None

        doc_ref.on_snapshot(on_snapshot)
        print("[ProfileListener] Started Firestore real-time profile listener.")
    except Exception as e:
        print(f"[ProfileListener] Failed to start listener (possibly missing face_recognition/dependencies): {e}")


# =============================================================================
#  BASE64 IMAGE ENCODING
# =============================================================================
def encode_image_base64(frame):
    """
    Resize frame to 320x240 and encode as base64 JPEG string.
    Stored directly in Firestore as 'imageBase64' — no Storage needed.
    """
    if frame is None:
        return ""
    try:
        h, w = frame.shape[:2]
        if w > 320:
            scale = 320.0 / w
            frame = cv2.resize(frame, (320, int(h * scale)), interpolation=cv2.INTER_AREA)
        _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 35])
        b64 = __import__('base64').b64encode(buffer).decode('utf-8')
        print(f"[Image] Base64 encoded ({len(b64) // 1024} KB)")
        return b64
    except Exception as e:
        print(f"[Image] Base64 encode error: {e}")
        return ""


# =============================================================================
#  FIREBASE STORAGE - 
# =============================================================================
def upload_image_to_storage(local_path, timestamp, label):
    try:
        bucket    = storage.bucket()
        blob_path = f"detections/{timestamp}_{label}.jpg"
        blob      = bucket.blob(blob_path)
        blob.upload_from_filename(local_path, content_type="image/jpeg")
        blob.make_public()
        download_url = blob.public_url
        print(f"[Storage] Uploaded  -> {blob_path}")
        print(f"[Storage] URL       -> {download_url}")
        return download_url
    except Exception as e:
        print(f"[Storage] ERROR uploading image: {e}")
        return None


# =============================================================================
#  FIRESTORE - WRITE DETECTION DOCUMENT  (UNCHANGED)
# =============================================================================
def write_detection_to_firestore(label, confidence, image_url, image_base64, now):

    try:
        db  = firestore.client()
        ref = db.collection(FIRESTORE_COLLECTION).document()  # auto-ID
        severity = "critical" if label == "Human" else "normal"
        titles = {
            "Human": "Unauthorized Human Detected",
            "Owner": "Authorized Human Detected",
            "Cat":   "Cat Detected",
            "Dog":   "Dog Detected",
        }
        doc_type = "human" if label in ["Human", "Owner"] else label.lower()
        data = {
            "title":       titles.get(label, "Detection Alert"),
            "type":        doc_type,
            "date":        now.strftime("%d %b %Y"),
            "time":        now.strftime("%I:%M %p"),
            "timestamp":   firestore.SERVER_TIMESTAMP,
            "confidence":  round(confidence, 4),
            "severity":    severity,
            "image":       image_url if image_url else "",
            "imageBase64": image_base64 if image_base64 else "",
            "isRead":      False,
            "cameraName":  CAMERA_NAME,
        }
        ref.set(data)
        doc_id = ref.id
        print(f"[Firestore] Document written -> ID: {doc_id}")
        return doc_id
    except Exception as e:
        print(f"[Firestore] ERROR writing document: {e}")
        return None


# =============================================================================
#  FCM - PUSH NOTIFICATION  (UNCHANGED)
# =============================================================================
def send_push_notification(label, confidence, doc_id):
    try:
        titles = {
            "Human": "Unauthorized Human Detected",
            "Owner": "Authorized Human Detected",
            "Cat":   "Cat Detected",
            "Dog":   "Dog Detected",
        }
        bodies = {
            "Human": f"Unauthorized person detected by {CAMERA_NAME} ({confidence:.0%} confidence).",
            "Owner": f"Authorized human recognized by {CAMERA_NAME} at the gate.",
            "Cat":   f"A cat spotted by {CAMERA_NAME} ({confidence:.0%} confidence).",
            "Dog":   f"A dog spotted by {CAMERA_NAME} ({confidence:.0%} confidence).",
        }
        doc_type = "human" if label in ["Human", "Owner"] else label.lower()
        message = messaging.Message(
            notification=messaging.Notification(
                title=titles.get(label, "Detection Alert"),
                body=bodies.get(label, f"{label} detected by {CAMERA_NAME}."),
            ),
            data={
                "type":       doc_type,
                "confidence": str(round(confidence, 4)),
                "doc_id":     doc_id if doc_id else "",
                "cameraName": CAMERA_NAME,
            },
            topic="farm_alerts",
        )
        response = messaging.send(message)
        print(f"[FCM] Notification sent -> {response}")
    except Exception as e:
        print(f"[FCM] ERROR sending notification: {e}")


# =============================================================================
#  GPIO
# =============================================================================
ldr1   = DigitalInputDevice(17, pull_up=True)
ldr2   = DigitalInputDevice(27, pull_up=True)
buzzer = DigitalOutputDevice(18)
led1    = DigitalOutputDevice(22)  
led2 = DigitalOutputDevice(23)



def read_ldr1():
    return not ldr1.value

def read_ldr2():
    return not ldr2.value



# =============================================================================
#  BUZZER + LED
# =============================================================================
def buzz_human():
    print("[Buzzer] 3 beeps -> HUMAN")
    for i in range(3):
        buzzer.on();  led1.on(); led2.on(); time.sleep(0.2)
        buzzer.off(); led1.off(); led2.off(); time.sleep(0.15)
        print(f"         Beep {i+1}/3")


def buzz_cat():
    print("[Buzzer] 5 beeps -> CAT")
    for i in range(5):
        buzzer.on();  led1.on(); led2.on(); time.sleep(0.15)
        buzzer.off(); led1.off(); led2.off(); time.sleep(0.1)
        print(f"         Beep {i+1}/5")


def buzz_dog():
    print("[Buzzer] 7 beeps -> DOG")
    for i in range(7):
        buzzer.on();  led1.on(); led2.on(); time.sleep(0.15)
        buzzer.off(); led1.off(); led2.off(); time.sleep(0.1)
        print(f"         Beep {i+1}/7")


def trigger_buzzer(label):
    if label in ["Human", "Unauthorized Human"]:
        buzz_human()
    elif label == "Cat":
        buzz_cat()
    elif label == "Dog":
        buzz_dog()


# =============================================================================
#  ARDUINO SERIAL READER (ultrasonic sensor fusion)
# =============================================================================
def start_serial_reader():
    def _reader_loop():
        global US1_DIST, US2_DIST, US_LAST_UPDATE, ARDUINO_CONNECTED
        while True:
            try:
                with serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=SERIAL_TIMEOUT_S) as ser:
                    time.sleep(2)  
                    ser.reset_input_buffer()
                    ARDUINO_CONNECTED = True
                    print(f"[Serial] Connected to Arduino on {SERIAL_PORT}")
                    while True:
                        line = ser.readline().decode("utf-8", errors="ignore").strip()
                        if not line:
                            continue
                        if line == "READY":
                            print("[Serial] Arduino reported READY")
                            continue
                        if line.startswith("US1:") and ",US2:" in line:
                            try:
                                part1, part2 = line.split(",")
                                d1 = int(part1.split(":")[1])
                                d2 = int(part2.split(":")[1])
                                with US_LOCK:
                                    US1_DIST = d1
                                    US2_DIST = d2
                                    US_LAST_UPDATE = time.time()
                                    US1_HISTORY.append(d1)
                                    US2_HISTORY.append(d2)
                            except (ValueError, IndexError):
                                pass 
            except Exception as e:
                ARDUINO_CONNECTED = False
                print(f"[Serial] Arduino not reachable on {SERIAL_PORT} ({e}); retrying in 3s...")
                time.sleep(3)

    thread = threading.Thread(target=_reader_loop, daemon=True)
    thread.start()
    print("[Serial] Background ultrasonic reader thread started")


def read_ultrasonic():
    with US_LOCK:
        d1, d2, last = US1_DIST, US2_DIST, US_LAST_UPDATE
    is_fresh = (time.time() - last) < 2.0
    return d1, d2, is_fresh


def read_ultrasonic_consensus():

    with US_LOCK:
        h1 = list(US1_HISTORY)
        h2 = list(US2_HISTORY)
        last = US_LAST_UPDATE
    is_fresh = (time.time() - last) < 2.0
    if not is_fresh or len(h1) < US_CONSENSUS_REQUIRED:
        return False, False
    us1_votes = sum(1 for d in h1 if d < ULTRASONIC_GATE_CM)
    us2_votes = sum(1 for d in h2 if d < ULTRASONIC_GATE_CM)
    return (us1_votes >= US_CONSENSUS_REQUIRED), (us2_votes >= US_CONSENSUS_REQUIRED)


# =============================================================================
#  CAMERA - Arducam IMX519 via Picamera2 (CSI)
# =============================================================================
def init_camera():

    global picam2
    picam2 = Picamera2()
    config = picam2.create_video_configuration(
        main={"size": CAMERA_RESOLUTION, "format": "RGB888"}
    )
    picam2.configure(config)
    picam2.start()
    try:
        picam2.set_controls({"AfMode": 2})
        print("[Camera] Continuous autofocus enabled")
    except Exception as e:
        print(f"[Camera] Autofocus control not available/applicable: {e}")
    time.sleep(CAMERA_WARMUP_S)
    print("[Camera] Arducam IMX519 initialised via Picamera2")


def capture_frame():

    try:
        frame = picam2.capture_array()
        return frame
    except Exception as e:
        print(f"[Camera] capture error: {e}")
        return None


# =============================================================================
#  DRAW BOUNDING BOXES ON FRAME 
# =============================================================================
def draw_detections(frame, model):
    results = model(frame, verbose=False)
    for result in results:
        for box in result.boxes:
            cls_id = int(box.cls[0])
            conf   = float(box.conf[0])
            if cls_id in TARGET_CLASSES and conf >= 0.4:
                label = TARGET_CLASSES[cls_id]
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                if label == "Human" and OWNER_ENCODING is not None:
                    h, w = frame.shape[:2]
                    y1_c, y2_c = max(0, y1), min(h, y2)
                    x1_c, x2_c = max(0, x1), min(w, x2)
                    crop = frame[y1_c:y2_c, x1_c:x2_c]
                    if crop.size > 0:
                        try:
                            import face_recognition
                            rgb_crop = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                            face_locs = face_recognition.face_locations(rgb_crop)
                            face_encs = face_recognition.face_encodings(rgb_crop, face_locs)
                            for fe in face_encs:
                                matches = face_recognition.compare_faces([OWNER_ENCODING], fe, tolerance=0.55)
                                if matches[0]:
                                    label = "Owner"
                                    break
                        except Exception:
                            pass

                color = CLASS_COLORS.get(label, (255, 255, 255))
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(
                    frame,
                    f"{label} {conf:.0%}",
                    (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, color, 2,
                )
    return frame


def add_status_bar(frame, b1, b2, us1, us2, status_text="Monitoring...", muted=False):
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (w, 65), (30, 30, 30), -1)
    ldr1_color = (0, 0, 255) if b1 else (0, 255, 0)
    ldr2_color = (0, 0, 255) if b2 else (0, 255, 0)
    cv2.putText(frame, f"LDR1: {'BLOCKED' if b1 else 'clear'}",
                (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, ldr1_color, 2)
    cv2.putText(frame, f"LDR2: {'BLOCKED' if b2 else 'clear'}",
                (180, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, ldr2_color, 2)
    cv2.putText(frame, status_text,
                (350, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
    us1_color = (0, 0, 255) if us1 < ULTRASONIC_GATE_CM else (0, 255, 0)
    us2_color = (0, 0, 255) if us2 < ULTRASONIC_GATE_CM else (0, 255, 0)
    cv2.putText(frame, f"US1: {us1}cm", (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.55, us1_color, 2)
    cv2.putText(frame, f"US2: {us2}cm", (180, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.55, us2_color, 2)
    mute_color = (0, 165, 255) if muted else (0, 255, 0)
    cv2.putText(frame, f"Alerts: {'MUTED' if muted else 'ON'}",
                (350, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.55, mute_color, 2)
    ts = datetime.datetime.now().strftime('%H:%M:%S')
    cv2.putText(frame, ts, (w - 90, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 2)
    return frame


# =============================================================================
#  YOLO 7-FRAME VOTING ANALYSIS  
# =============================================================================
def analyze_frames(model):
 
    print(f"\n[YOLO] Analyzing {FRAMES_TO_CAPTURE} frames...")
    print("-" * 45)
    votes       = defaultdict(int)
    conf_totals = defaultdict(float)
    best_frame  = None
    best_conf   = 0.0
    for i in range(FRAMES_TO_CAPTURE):
        frame = capture_frame()
        if frame is None:
            print(f"  Frame {i+1}/{FRAMES_TO_CAPTURE}: No frame received")
            continue
        results     = model(frame, verbose=False)
        frame_label = None
        frame_conf  = 0.0
        for result in results:
            for box in result.boxes:
                cls_id = int(box.cls[0])
                conf   = float(box.conf[0])
                if cls_id in TARGET_CLASSES and conf > frame_conf:
                    frame_conf  = conf
                    frame_label = TARGET_CLASSES[cls_id]
        if frame_label and frame_conf >= MIN_CONFIDENCE:
            votes[frame_label]       += 1
            conf_totals[frame_label] += frame_conf
            if frame_conf > best_conf:
                best_conf  = frame_conf
                best_frame = frame.copy()
            print(f"  Frame {i+1}/{FRAMES_TO_CAPTURE}: {frame_label} ({frame_conf:.0%}) confirmed")
        else:
            raw = f"{frame_label} ({frame_conf:.0%})" if frame_label else "nothing"
            print(f"  Frame {i+1}/{FRAMES_TO_CAPTURE}: {raw} (below threshold, skipped)")
        time.sleep(0.1)
    print("-" * 45)
    if not votes:
        print("[YOLO] No confident detection in any frame")
        return None, 0.0, best_frame
    print("\n[YOLO] Vote summary:")
    for label, count in sorted(votes.items(), key=lambda x: -x[1]):
        avg = conf_totals[label] / count
        print(f"         {label}: {count}/{FRAMES_TO_CAPTURE} votes | avg: {avg:.0%}")
    scores      = {l: (votes[l] / FRAMES_TO_CAPTURE) * (conf_totals[l] / votes[l]) for l in votes}
    final_label = max(scores, key=scores.get)
    final_conf  = conf_totals[final_label] / votes[final_label]
    print(f"\n[YOLO] Winner -> {final_label} "
          f"({votes[final_label]}/{FRAMES_TO_CAPTURE} votes | avg: {final_conf:.0%})")
    return final_label, final_conf, best_frame


# =============================================================================
#  SAVE IMAGE LOCALLY  (UNCHANGED)
# =============================================================================
def compress_and_save_image(frame, label, timestamp):
    if frame is None:
        return None
    os.makedirs(SAVE_DIR, exist_ok=True)
    h, w = frame.shape[:2]
    if w > 640:
        scale  = 640.0 / w
        frame  = cv2.resize(frame, (640, int(h * scale)), interpolation=cv2.INTER_AREA)
    path = os.path.join(SAVE_DIR, f"{timestamp}_{label}.jpg")
    cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
    size_kb = os.path.getsize(path) / 1024
    print(f"[Image] Saved ({size_kb:.1f} KB) -> {path}")
    return path


# =============================================================================
#  SENSOR FUSION FOR THE INITIAL (PRE-YOLO) GUESS
# =============================================================================
def get_fused_trigger_state():
    b1 = read_ldr1()
    b2 = read_ldr2()
    us1, us2, us_fresh = read_ultrasonic()
    us1_triggered, us2_triggered = read_ultrasonic_consensus()

    low_triggered  = b1 or us1_triggered
    high_triggered = b2 or us2_triggered

    trigger = low_triggered
    initial_guess = "Human" if (low_triggered and high_triggered) else "Animal"

    return {
        "b1": b1, "b2": b2,
        "us1": us1, "us2": us2, "us_fresh": us_fresh,
        "low_triggered": low_triggered, "high_triggered": high_triggered,
        "trigger": trigger, "initial_guess": initial_guess,
    }


# =============================================================================
#  MAIN LOOP
# =============================================================================
def main():
    os.makedirs(SAVE_DIR, exist_ok=True)

    init_oled() 
    
    firebase_ready = init_firebase()
    if firebase_ready:
        start_profile_listener()

    storage_ready = False
    if firebase_ready:
        storage_ready = check_storage_available()

    start_serial_reader()
    init_camera()

    print("\n[System] Loading YOLOv8s model...")
    model = YOLO('yolov8s.pt')
    print("[System] YOLOv8s ready")

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, 800, 500)

    print("\n" + "=" * 55)
    print("  Fisheries Farm - Live Detection + Firebase")
    print("=" * 55)
    print(f"  Camera   : {CAMERA_NAME} (Arducam IMX519, CSI)")
    print("  Human -> 3 beeps | Cat -> 5 beeps | Dog -> 7 beeps")
    print(f"  Firebase : {'READY' if firebase_ready else 'NOT CONNECTED (local-only)'}")
    print(f"  Images   : {'ENABLED' if storage_ready else 'DISABLED (upgrade to Blaze plan)'}")
    print(f"  Arduino  : streaming on {SERIAL_PORT} (auto-reconnect enabled)")
    print("  Press Q in camera window to quit")
    print("=" * 55 + "\n")

    detection_count = 0
    status_text     = "Monitoring..."
    cooldown        = False

    try:
        while True:
            fusion = get_fused_trigger_state()
            b1, b2 = fusion["b1"], fusion["b2"]
            us1, us2 = fusion["us1"], fusion["us2"]

            frame = capture_frame()
            if frame is not None:
                display_frame = draw_detections(frame.copy(), model)
                display_frame = add_status_bar(display_frame, b1, b2, us1, us2, status_text)
                cv2.imshow(WINDOW_NAME, display_frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                print("\n[System] Q pressed - quitting")
                break

            print(
                f"\r  LDR1:{'BLOCKED' if b1 else 'clear  '} US1:{us1:>3}cm | "
                f"LDR2:{'BLOCKED' if b2 else 'clear  '} US2:{us2:>3}cm | ",
                end='', flush=True,
            )

            if fusion["trigger"] and not cooldown:
                cooldown        = True
                status_text     = "INTRUDER DETECTED!"
                detection_count += 1
                now       = datetime.datetime.now()
                timestamp = now.strftime('%Y%m%d_%H%M%S')
                now_str   = now.strftime('%Y-%m-%d %H:%M:%S')

                print(f"\n\n{'!' * 55}")
                print(f"  INTRUSION #{detection_count} | {now_str}")
                print(f"{'!' * 55}")

                laser_guess = fusion["initial_guess"]
                print(f"[Sensors] LDR1:{'BLOCKED' if b1 else 'clear'} US1:{us1}cm | "
                      f"LDR2:{'BLOCKED' if b2 else 'clear'} US2:{us2}cm")
                print(f"[Sensors] Fused initial guess: {laser_guess}")

                yolo_label, confidence, best_frame = analyze_frames(model)

                if yolo_label is None:
                    print("\n[System] False alarm. Sensors tripped, but YOLO saw nothing. Skipping Firebase & alerts.")
                    update_oled_display("False Alarm", "No Target Seen")
                    print("[System] Cooldown 3 seconds...")
                    time.sleep(3)
                    cooldown    = False
                    status_text = "Monitoring..."
                    update_oled_display("Monitoring...", "System Clean")
                    print("\n" + "=" * 55)
                    print("  Resuming monitoring...")
                    print("=" * 55 + "\n")
                    continue

                final_label = yolo_label
                source_info = f"YOLO ({confidence:.0%} avg)"

                is_owner = False
                if final_label == "Human" and best_frame is not None and OWNER_ENCODING is not None:
                    try:
                        import face_recognition
                        rgb_frame = cv2.cvtColor(best_frame, cv2.COLOR_BGR2RGB)
                        face_locs = face_recognition.face_locations(rgb_frame)
                        face_encs = face_recognition.face_encodings(rgb_frame, face_locs)
                        for fe in face_encs:
                            matches = face_recognition.compare_faces([OWNER_ENCODING], fe, tolerance=0.55)
                            if matches[0]:
                                is_owner = True
                                break
                    except Exception as e:
                        print(f"[FaceRec] Error comparing faces: {e}")
                
                if is_owner:
                    final_label = "Owner"
                    source_info = "Face recognition (Owner matched)"
                elif final_label == "Human":
                    final_label = "Unauthorized Human"

                update_oled_display("Detected Target:", final_label.upper())

                image_url    = None
                image_base64 = ""
                local_path   = None
                if best_frame is not None:
                    annotated    = draw_detections(best_frame.copy(), model)
                    local_path   = compress_and_save_image(annotated, final_label, timestamp)
                    image_base64 = encode_image_base64(annotated)

                if storage_ready and local_path:
                    image_url = upload_image_to_storage(local_path, timestamp, final_label)

                doc_id = None
                if firebase_ready:
                    doc_id = write_detection_to_firestore(
                        label        = final_label,
                        confidence   = confidence if yolo_label else 0.5,
                        image_url    = image_url,
                        image_base64 = image_base64,
                        now          = now,
                    )

                if firebase_ready:
                    send_push_notification(
                        label      = final_label,
                        confidence = confidence if yolo_label else 0.5,
                        doc_id     = doc_id,
                    )

                print(f"\n{'*' * 55}")
                print(f"  FINAL RESULT : {final_label.upper()}")
                print(f"  SOURCE       : {source_info}")
                print(f"  TIME         : {now_str}")
                print(f"  IMAGE        : {'URL uploaded' if image_url else ('Base64 stored' if image_base64 else 'none')}")
                print(f"  DOC ID       : {doc_id or '(not written)'}")
                print(f"{'*' * 55}\n")

                status_text = f"RESULT: {final_label.upper()}"

                trigger_buzzer(final_label)

                print("[System] Cooldown 3 seconds...")
                time.sleep(3)
                cooldown    = False
                status_text = "Monitoring..."
                print("\n" + "=" * 55)
                print("  Resuming monitoring...")
                print("=" * 55 + "\n")

            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\n\n[System] Stopped by user (Ctrl+C)")
        print(f"[System] Total detections this session: {detection_count}")
    finally:
        cv2.destroyAllWindows()
        if picam2 is not None:
            try:
                picam2.stop()
            except Exception:
                pass
        if oled is not None:
            try:
                oled.fill(0)
                oled.show()
            except Exception:
                pass
        print("[System] Camera, OLED, and OpenCV cleaned up")


if __name__ == '__main__':
    main()
