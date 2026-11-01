# Saidcue demand validation plan

Goal: decide whether to build billing, hosted AI, the cloud library and sync, based on evidence rather than guesses.

## Offer being tested
Free: local dictation, a few voice triggers, visual editor, 30 hosted AI questions/month (BYOK is being removed).
Pro (target $9/mo): hosted AI co-pilot, unlimited triggers, cloud library of packs/sounds/overlays, settings sync.

## Success bar (decide after 4 weeks of traffic)
| Signal | Go | Iterate | Stop |
|---|---|---|---|
| Waitlist emails from targeted channels | 200+ | 80-199 | <80 |
| Visitor-to-signup conversion | >=8% | 3-8% | <3% |
| Signups naming a Pro feature they would pay $9 for | >=60% | 35-60% | <35% |
| Signups answering "None, stay on Free" | <30% | 30-50% | >50% |
| Replies to a follow-up survey email | >=15% | 8-15% | <8% |

Notes:
- Traffic must be targeted (streamer/teacher/GM communities), not friends and family.
- Tag each channel with `?src=` (e.g. `?src=reddit-obs`) so conversion per channel is visible. The form sends `src`, `role` and `pro_feature`. Price is fixed at $9 on the page (users do not pick it), so the signal is which feature justifies paying.
- Segment results by `role` and `pro_feature`. Pick the segment with the strongest paid-feature signal as the launch audience.

## Free AI allowance: signals to track once the product ships
- Share of free users who use any AI question (activation).
- Share who hit the 30/month cap, and how fast (day of month). Hitting the cap early is the strongest upgrade signal.
- Upgrade rate among cap-hitters vs. everyone else.
- Cost per free user per month (target: a few cents). If it exceeds that, lower the cap or use a cheaper model.
- If almost nobody reaches 30, the cap is too generous; if most hit it in week one, it is too tight to show value.

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
