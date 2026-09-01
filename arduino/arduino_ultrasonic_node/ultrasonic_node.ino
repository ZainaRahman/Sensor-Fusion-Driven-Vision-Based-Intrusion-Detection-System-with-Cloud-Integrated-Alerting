
#define TRIG1_PIN 3
#define ECHO1_PIN 4
#define TRIG2_PIN 5
#define ECHO2_PIN 6

const unsigned long SEND_INTERVAL_MS = 100;   // ~10 readings/sec
unsigned long lastSendTime = 0;

long readDistanceCM(int trigPin, int echoPin) {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  long duration = pulseIn(echoPin, HIGH, 30000); // 30ms timeout (~5m range)
  if (duration == 0) return 999;
  long cm = duration * 0.034 / 2;
  if (cm <= 0 || cm > 500) return 999;
  return cm;
}

void setup() {
  Serial.begin(9600);

  pinMode(TRIG1_PIN, OUTPUT);
  pinMode(ECHO1_PIN, INPUT);
  pinMode(TRIG2_PIN, OUTPUT);
  pinMode(ECHO2_PIN, INPUT);

  digitalWrite(TRIG1_PIN, LOW);
  digitalWrite(TRIG2_PIN, LOW);

  delay(500); // let sensors settle
  Serial.println("READY");
}

void loop() {
  unsigned long now = millis();
  if (now - lastSendTime >= SEND_INTERVAL_MS) {
    lastSendTime = now;

    long dist1 = readDistanceCM(TRIG1_PIN, ECHO1_PIN);
    long dist2 = readDistanceCM(TRIG2_PIN, ECHO2_PIN);

    Serial.print("US1:");
    Serial.print(dist1);
    Serial.print(",US2:");
    Serial.println(dist2);
  }
}
