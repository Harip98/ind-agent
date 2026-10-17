---
doc_id: SOP-PUMP-07
version: 2
doc_type: sop
effective_date: 2026-03-01
superseded_by: null
tenant: _shared
asset_models: [CP-200]
license: self-authored
---
# CP-200 Pump Bearing Temperature High — Troubleshooting

## 1 Scope

This procedure applies to CP-200 centrifugal process pumps when the bearing temperature alarm (TT-101 high) is active or the bearing temperature trend is rising. It covers diagnosis by symptom pattern and the corrective action for each cause. It does not cover motor winding temperature alarms.

## 2 Safety

Do not open the bearing housing or cooling water connections while the pump is running. Apply lock-out/tag-out (LOTO) on the motor breaker and close suction and discharge isolation valves before any intrusive work. Bearing housings above 70 °C cause burns; use gloves and allow cooling. Never bypass the high-temperature trip.

## 3 Alarm Description

TT-101 measures pump drive-end bearing temperature. The high alarm is set at 85 °C; normal operation is 58–66 °C. TT-102 measures the bearing cooling water return temperature; its high alarm is 38 °C and normal is 26–30 °C. A TT-101 alarm on its own does not identify the cause: always review TT-102, discharge flow FT-101, motor current IT-101 and vibration VT-101 over the six hours before the alarm.

## 4 Diagnosis by Symptom Pattern

### 4.1 Cooling Water Restriction

Pattern: TT-101 and TT-102 rise together over one to several hours while discharge flow FT-101 and motor current IT-101 stay normal and vibration is unchanged. The rising cooling water return temperature shows that heat is not being removed from the bearing housing.

Likely causes: partially closed cooling water supply valve, blocked cooling water strainer, scaled cooling jacket, or low cooling water header pressure. Check the cooling water supply valve position, measure cooling water flow with the portable ultrasonic meter (minimum 1.2 m³/h), and inspect the strainer differential pressure.

### 4.2 Bearing Wear or Lubrication Failure

Pattern: VT-101 vibration rises gradually over days, followed by a rising TT-101. Motor current may increase slightly (2–5 A). Cooling water return temperature stays normal.

Likely causes: degraded or contaminated grease, wrong grease quantity, bearing fatigue or misalignment. Check the lubrication record against the interval in MAN-CP200, take a vibration spectrum, and plan bearing replacement if velocity exceeds 7.1 mm/s.

### 4.3 Instrument Fault

Pattern: TT-101 rises steadily or jumps while every related signal — TT-102, FT-101, IT-101, VT-101 — stays flat. A real bearing problem almost always moves at least one related signal.

Verify the transmitter with a portable reference thermometer at the bearing housing according to SOP-INST-03 before taking the pump out of service.

### 4.4 Dry Running or Loss of Suction

Pattern: discharge flow FT-101 and discharge pressure PT-101 collapse within minutes, motor current drops, and TT-101 rises quickly. See SOP-PUMP-11 for the full procedure.

## 5 Corrective Actions

### 5.1 Cooling Water Restriction

Restore cooling water flow: open the supply valve fully, clean the strainer (use your site-specific isolation and strainer procedure), and flush the cooling jacket if flow remains below 1.2 m³/h. Confirm TT-102 returns below 30 °C and TT-101 begins to fall within 30 minutes.

### 5.2 Bearing Wear

Re-grease per MAN-CP200 only if the lubrication interval was missed. If vibration remains above 7.1 mm/s, schedule bearing replacement and run the standby pump.

### 5.3 Instrument Fault

Replace or recalibrate the transmitter per SOP-INST-03. Do not change alarm limits to suppress the alarm.

## 6 Return to Service

After corrective work, start the pump with the standby pump available, monitor TT-101, TT-102 and VT-101 for two hours, and record the cause and action in the maintenance log.
