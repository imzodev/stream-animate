# Saidcue demand validation plan

Goal: decide whether to build billing, hosted AI, the cloud library and sync, based on evidence rather than guesses.

## Offer being tested
Free: local dictation, a few voice triggers, visual editor, bring-your-own AI key.
Pro (target $9/mo): hosted AI co-pilot, unlimited triggers, cloud library of packs/sounds/overlays, settings sync.

## Success bar (decide after 4 weeks of traffic)
| Signal | Go | Iterate | Stop |
|---|---|---|---|
| Waitlist emails from targeted channels | 200+ | 80-199 | <80 |
| Visitor-to-signup conversion | >=8% | 3-8% | <3% |
| Signups choosing $9 or more | >=30% | 15-30% | <15% |
| Signups choosing "Nothing" | <40% | 40-60% | >60% |
| Replies to a follow-up survey email | >=15% | 8-15% | <8% |

Notes:
- Traffic must be targeted (streamer/teacher/GM communities), not friends and family.
- Tag each channel with `?src=` (e.g. `?src=reddit-obs`) so conversion per channel is visible. The form sends `src`, `role` and `price`.
- Segment results by `role`. Pick the one segment with the best price signal as the launch audience.

## Willingness-to-pay beyond the form
1. Email each signup after signup asking: "What would make you pay for this? What do you use today?"
2. Run 8-10 short calls with signups who picked $9 or more.
3. Optional stronger signal: a refundable founder price ($49/year) via a payment link, 20 buyers = strong go.

## Before building billing, build in this order
1. Hosted AI (cheapest to ship, clearest value). Cap usage (e.g. N questions/day) and track cost per user.
2. Trigger pack library (needs content; seed with 10-20 packs yourself).
3. Settings sync (last, lowest standalone appeal).

## Pending
- Connect `WAITLIST_ENDPOINT` in `landing/index.html` (Formspree/Basin/own endpoint).
- Host the page and add channel links.
