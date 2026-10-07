# Data request for the virtual card audit

Thank you for helping with the virtual card audit. We check every Expedia Collect and Payments by Booking.com virtual card your hotel received against your PMS and card processor records, and list any card that was not charged, was charged the wrong amount, or was charged but never settled. You get the list back with the evidence for each item so your team can collect the money.

This page lists exactly what we need. It usually takes 1 to 2 hours to pull.

## What to send, per hotel

**Date range:** the last 12 months at minimum. 13 to 18 months is better, because cards from older stays can still be claimed.

**Format:** CSV or Excel (.xlsx). Please do not send PDFs; we cannot read them. If a report only offers PDF, look for an "Export", "Download as CSV" or "Excel" option, or tell us and we will find another way.

**Card numbers:** please make sure card numbers are masked (for example `XXXX1234`). We only use the last 4 digits. If a file arrives with full card numbers, our software drops them automatically and never stores them, but we would rather not receive them at all.

### 1. PMS reservation export

One row per reservation, all channels if possible (at minimum every Expedia and Booking.com booking), including cancellations and no-shows.

| Column we need | Notes |
|---|---|
| PMS confirmation number | |
| OTA confirmation number | Often called external reference, channel confirmation or third-party confirmation |
| Channel or source | Expedia, Booking.com, direct, and so on |
| Rate plan or payment type | Anything that shows whether the OTA collected payment (virtual card) or the guest paid the hotel |
| Guest last name | |
| Arrival and departure dates | |
| Status | Checked out, in house, cancelled, no-show, reserved |
| Room revenue, tax, folio total | |

### 2. PMS payment postings (ledger)

One row per payment posted to a folio, including reversals and adjustments.

| Column we need | Notes |
|---|---|
| Confirmation or folio number | Must match the reservation export |
| Posting date | |
| Payment type or method | For example "Virtual Card - Expedia", Visa, Mastercard |
| Amount | Negative for reversals, or a separate reversal flag |
| Card last 4 | Masked card number is fine |

### 3. Expedia virtual card report (Expedia Collect)

From Expedia Partner Central, the report listing virtual cards for Expedia Collect bookings.

| Column we need | Notes |
|---|---|
| Reservation or itinerary ID | |
| Card amount | |
| Activation date and expiry date | |
| Card last 4 | |
| Guest name and check-in date | Helps us match bookings whose confirmation number was not entered in the PMS |
| Charge or card status | If shown |

### 4. Booking.com virtual card or payments report (Payments by Booking.com)

From the Booking.com extranet, the report listing virtual cards or payments for bookings paid through Booking.com.

| Column we need | Notes |
|---|---|
| Reservation number | |
| Card amount | |
| Activation date and expiry date | |
| Card last 4 | |
| Guest name and arrival date | |
| Payment status | If shown |

### 5. Card processor or gateway settlement report

From your card processor or payment gateway, the transaction-level settlement or funding report.

| Column we need | Notes |
|---|---|
| Transaction date and settlement date | |
| Amount | |
| Card last 4 | |
| Transaction type | Sale, refund, void, chargeback |
| Authorization code | |
| Reference, invoice or folio number | If available. It improves matching |

### 6. Property basics

A short note or email with:

- Hotel name, brand and room count
- Management company
- PMS name and version
- Which OTAs you use with virtual cards (Expedia Collect, Payments by Booking.com, others)
- Card processor or gateway name

## How to send

Put the files in a shared folder or send them by secure file transfer. Please do not email files that contain full card numbers. File names do not matter, but one folder per hotel helps.

## What happens next

We import the files, which usually takes a day. We may come back with a question if a column is unclear or a date range is short. You then receive the list of flagged cards with the evidence for each, sorted by priority, so your team can start with the largest amounts and the cards closest to expiry.

Nothing is charged or changed in your systems by us. Every item is reviewed by a person before anyone acts on it.
