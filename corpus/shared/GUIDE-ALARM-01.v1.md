---
doc_id: GUIDE-ALARM-01
version: 1
doc_type: guide
effective_date: 2025-08-01
superseded_by: null
tenant: _shared
asset_models: [ANY]
license: self-authored
---
# Alarm Management — Nuisance and Chattering Alarms

## 1 Purpose

This guide helps engineers recognise alarms that do not reflect a real process condition, so that investigation effort goes to genuine problems.

## 2 Nuisance Spikes

A nuisance spike is an alarm caused by one or two samples above the limit with no change in any related signal before or after. Typical causes are electrical noise, a loose connection, or a momentary transmitter glitch. Confirm by reviewing the related signals for the same asset: if nothing else moved and the value returned to normal within a few minutes, record the alarm as a nuisance and monitor for recurrence.

## 3 Chattering Alarms

A chattering alarm raises and clears repeatedly within a short period because the value hovers near the limit. It often appears while a real fault develops slowly. Treat the first alarm of a chattering sequence as the start of the event and investigate the underlying trend.

## 4 Remedies

Use an alarm deadband (typically 2 % of the limit) and an on-delay of 10–60 seconds for noisy signals. Never raise an alarm limit to stop nuisance alarms without an engineering review.
