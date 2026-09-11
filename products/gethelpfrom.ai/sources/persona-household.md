## Money & bills

### Medical bills & EOBs
- id: eob_still_cant_tell_what_we_owe | bucket: Money & bills | sub: Medical bills & EOBs | text: "I open an EOB and still can't tell if we owe $40 or $400." | pain: eob_unreadable | solution: eob_explainer
- id: hospital_bill_months_later | bucket: Money & bills | sub: Medical bills & EOBs | text: "A hospital bill shows up three months later for a visit I thought insurance already closed." | pain: surprise_balance_bill | solution: eob_explainer
- id: mom_copay_then_second_bill | bucket: Money & bills | sub: Medical bills & EOBs | text: "I pay Mom's copay at the desk and then get another bill at home for the same visit." | pain: duplicate_medical_charge | solution: bill_pay_agent
- id: kids_urgent_care_drawer | bucket: Money & bills | sub: Medical bills & EOBs | text: "The kids' urgent-care bills sit in a drawer because I don't know which ones are actually paid." | pain: unfinished_medical_bills | solution: document_inbox

### Subscriptions & auto-pays
- id: mystery_streaming_on_joint_card | bucket: Money & bills | sub: Subscriptions & auto-pays | text: "A streaming charge hits the joint card that neither of us remembers turning on." | pain: forgotten_subscription | solution: subscription_audit
- id: kids_apps_auto_renew | bucket: Money & bills | sub: Subscriptions & auto-pays | text: "The kids' school and game apps auto-renew and I only notice on the statement." | pain: kids_app_renewals | solution: subscription_audit
- id: mom_pharmacy_autoship_after_switch | bucket: Money & bills | sub: Subscriptions & auto-pays | text: "Mom's pharmacy auto-ship keeps charging after we already switched her meds." | pain: stale_autoship | solution: subscription_audit
- id: canceled_still_bills | bucket: Money & bills | sub: Subscriptions & auto-pays | text: "I cancel a subscription and it still bills us the next month." | pain: cancel_didnt_stick | solution: subscription_audit

### Who paid what
- id: spouse_handles_it_until_they_dont | bucket: Money & bills | sub: Who paid what | text: "My spouse 'handles the bills' until a late notice shows up and they didn't." | pain: spouse_bill_handoff | solution: bill_pay_agent
- id: both_thought_other_paid_electric | bucket: Money & bills | sub: Who paid what | text: "We both think the other one paid the electric bill until it's past due." | pain: double_assume_paid | solution: bill_pay_agent
- id: sitter_on_my_card | bucket: Money & bills | sub: Who paid what | text: "I put the sitter on my card and then forget to pull it from the joint account." | pain: unreimbursed_household_spend | solution: bill_pay_agent
- id: 529_skips_a_month | bucket: Money & bills | sub: Who paid what | text: "Our 529 contribution skips a month and nobody notices until the quarterly email." | pain: missed_family_transfer | solution: bill_pay_agent

### Late fees & tax packets
- id: late_fee_most_months | bucket: Money & bills | sub: Late fees & tax packets | text: "I pay at least one late fee most months." | pain: recurring_late_fees | solution: bill_pay_agent
- id: hoa_property_tax_penalty | bucket: Money & bills | sub: Late fees & tax packets | text: "The HOA and property-tax envelopes sit unopened until a penalty letter arrives." | pain: ignored_homeowner_dues | solution: bill_pay_agent
- id: tax_season_daycare_hunt | bucket: Money & bills | sub: Late fees & tax packets | text: "Tax season starts and I'm still hunting W-2s, 1099s, and the daycare receipt." | pain: scattered_tax_docs | solution: document_inbox
- id: mom_care_receipts_three_places | bucket: Money & bills | sub: Late fees & tax packets | text: "I can't add up Mom's medical expenses because the receipts are in three different places." | pain: caregiver_expense_scatter | solution: document_inbox

## Home & paperwork

### School forms
- id: permission_slip_in_backpack | bucket: Home & paperwork | sub: School forms | text: "A permission slip lives in a backpack until the morning it's due." | pain: last_minute_school_form | solution: form_filler
- id: same_emergency_contact_every_season | bucket: Home & paperwork | sub: School forms | text: "I fill out the same emergency-contact form for school, camp, and soccer every season." | pain: repeated_kid_forms | solution: form_filler
- id: iep_packet_on_counter | bucket: Home & paperwork | sub: School forms | text: "The IEP packet sits on the counter because I don't know which pages need a signature." | pain: unread_school_packet | solution: form_filler
- id: sports_physical_three_portals | bucket: Home & paperwork | sub: School forms | text: "Sports physicals and shot records are in three portals and I still can't print one by Friday." | pain: split_health_records | solution: document_inbox

### Parent-care papers
- id: mom_medicare_mail_to_our_house | bucket: Home & paperwork | sub: Parent-care papers | text: "Mom's Medicare mail comes to our house and I don't know what actually needs an answer." | pain: elder_mail_triage | solution: eob_explainer
- id: cant_find_poa_at_doctors | bucket: Home & paperwork | sub: Parent-care papers | text: "A doctor's office asks for Mom's power of attorney and I still can't put my hands on it." | pain: missing_poa | solution: document_inbox
- id: prior_auth_in_purse | bucket: Home & paperwork | sub: Parent-care papers | text: "The pharmacy wants a prior-auth form that's in her purse or our junk drawer." | pain: buried_prior_auth | solution: form_filler
- id: specialist_visit_no_notes | bucket: Home & paperwork | sub: Parent-care papers | text: "I leave her specialist visit with no notes and then can't remember what they changed." | pain: lost_care_instructions | solution: document_inbox

### Mail & the junk drawer
- id: kitchen_counter_mail_stack | bucket: Home & paperwork | sub: Mail & the junk drawer | text: "The kitchen counter is a stack of mail I swear I'll sort this weekend." | pain: mail_pile | solution: document_inbox
- id: phone_scan_then_lost | bucket: Home & paperwork | sub: Mail & the junk drawer | text: "I photograph a form with my phone and then can't find it when school asks again." | pain: lost_phone_scan | solution: document_inbox
- id: warranties_in_two_inboxes | bucket: Home & paperwork | sub: Mail & the junk drawer | text: "Appliance warranties and HOA PDFs live in two inboxes and a drawer." | pain: scattered_home_pdfs | solution: document_inbox
- id: recycled_needed_letter | bucket: Home & paperwork | sub: Mail & the junk drawer | text: "I recycle a letter that turns out to be something we needed to keep." | pain: shredded_keep | solution: document_inbox

### Family calendar mix-ups
- id: conference_on_spouse_calendar | bucket: Home & paperwork | sub: Family calendar mix-ups | text: "A school conference is on my spouse's calendar and I find out the morning of." | pain: spouse_calendar_blind_spot | solution: form_filler
- id: two_practices_and_mom_overlap | bucket: Home & paperwork | sub: Family calendar mix-ups | text: "Two kids' practices and Mom's appointment land on the same afternoon and nobody flags it." | pain: sandwich_schedule_clash | solution: form_filler
- id: rsvp_forgot_to_tell_house | bucket: Home & paperwork | sub: Family calendar mix-ups | text: "I say yes to a birthday party and then forget to tell anyone else in the house." | pain: unshared_rsvp | solution: form_filler
- id: sticky_note_fell_off_fridge | bucket: Home & paperwork | sub: Family calendar mix-ups | text: "The pediatrician reminder is a sticky note that fell off the fridge." | pain: fridge_sticky_note | solution: document_inbox
