## Leads & sales

### Slow first reply
- id: facebook_lead_sat_6h | bucket: Leads & sales | sub: Slow first reply | text: "A Facebook lead sat 6 hours because I was on a job." | pain: first_reply_while_on_job | solution: speed_to_lead
- id: zillow_during_showing | bucket: Leads & sales | sub: Slow first reply | text: "A Zillow lead hits during a showing and I don't reply until I'm in the car at 9." | pain: first_reply_during_showing | solution: speed_to_lead
- id: rental_voicemail_at_handover | bucket: Leads & sales | sub: Slow first reply | text: "Someone calls about a rental while I'm at a handover and the voicemail is still unplayed." | pain: unplayed_lead_voicemail | solution: missed_call_textback
- id: google_lsa_silenced_on_install | bucket: Leads & sales | sub: Slow first reply | text: "I silence a Google lead ping to finish the install and they already booked the other guy." | pain: silenced_lead_ping | solution: speed_to_lead

### Warm leads go cold
- id: loved_the_house_no_checkin | bucket: Leads & sales | sub: Warm leads go cold | text: "The couple who loved the house Saturday went quiet because I never sent the 'just checking in' text." | pain: warm_lead_no_nurture | solution: lead_nurture
- id: condenser_quote_never_chased | bucket: Leads & sales | sub: Warm leads go cold | text: "I quoted a condenser last Tuesday and never followed up, and now they're not answering." | pain: quote_no_chase | solution: quote_followup
- id: turo_thread_starred_and_forgot | bucket: Leads & sales | sub: Warm leads go cold | text: "A guest asked about next month, I starred the Turo thread, and I still haven't answered." | pain: starred_thread_decay | solution: lead_nurture
- id: open_house_signins_in_truck | bucket: Leads & sales | sub: Warm leads go cold | text: "Open-house sign-ins from two weeks ago are still on a clipboard in the truck." | pain: paper_leads_uncalled | solution: lead_nurture

### Quoting takes forever
- id: driveway_notepad_unreadable | bucket: Leads & sales | sub: Quoting takes forever | text: "I scribble a price on a notepad in the driveway and then can't read it when I get home." | pain: handwritten_quote | solution: quote_builder
- id: monday_quote_still_in_my_head | bucket: Leads & sales | sub: Quoting takes forever | text: "They asked for a written quote on Monday and I'm still building it in my head." | pain: quote_not_sent | solution: quote_builder
- id: texted_a_number_they_wanted_pdf | bucket: Leads & sales | sub: Quoting takes forever | text: "I texted a round number, they wanted it on letterhead, and I never sent the PDF." | pain: quote_format_stall | solution: quote_followup
- id: rental_quote_calendar_and_old_dms | bucket: Leads & sales | sub: Quoting takes forever | text: "Every rental quote means I open the calendar, a spreadsheet, and three old DMs." | pain: quote_data_scatter | solution: quote_builder

### DMs on three apps
- id: buyer_instagram_email_and_text | bucket: Leads & sales | sub: DMs on three apps | text: "The same buyer DMed me on Instagram, emailed, and texted, and I answered two of the three." | pain: split_lead_threads | solution: inbox_unify
- id: facebook_cell_and_housecall | bucket: Leads & sales | sub: DMs on three apps | text: "Facebook, the business cell, and Housecall Pro all have the same AC lead and I don't know which thread is current." | pain: duplicate_lead_threads | solution: inbox_unify
- id: guest_in_turo_sms_and_email | bucket: Leads & sales | sub: DMs on three apps | text: "A guest is in Turo chat, SMS, and email and I keep repeating myself." | pain: guest_thread_scatter | solution: inbox_unify
- id: zillow_names_in_notes_app | bucket: Leads & sales | sub: DMs on three apps | text: "I copy names from Zillow into my notes app and still lose who wanted the Tuesday showing." | pain: lead_not_in_crm | solution: crm_capture

## Customer support

### Missed calls after hours
- id: nocool_voicemail_at_11pm | bucket: Customer support | sub: Missed calls after hours | text: "The no-cool call came in at 11pm and I found the voicemail at 7am — they already hired the 24-hour shop." | pain: after_hours_voicemail | solution: after_hours_sms
- id: saturday_lockouts_heard_monday | bucket: Customer support | sub: Missed calls after hours | text: "Saturday the van number filled up with lockout calls and I didn't hear them until Monday." | pain: weekend_call_pileup | solution: after_hours_sms
- id: see_it_tonight_ringer_off | bucket: Customer support | sub: Missed calls after hours | text: "A buyer texted 'can we see it tonight' at 8:30 and I was at my kid's game with the ringer off." | pain: after_hours_showing_request | solution: after_hours_sms
- id: lockbox_code_at_1am | bucket: Customer support | sub: Missed calls after hours | text: "The lockbox-code question hit Turo at 1am and I was asleep, so they left a one-star." | pain: overnight_guest_lockout | solution: after_hours_sms

### Status chase texts
- id: where_is_the_tech_four_times | bucket: Customer support | sub: Status chase texts | text: "The homeowner texts 'where is the tech' four times before I can pull over." | pain: eta_chase_texts | solution: eta_sms
- id: part_came_in_they_had_to_chase | bucket: Customer support | sub: Status chase texts | text: "I promised a callback when the part came in and they had to chase me two days later." | pain: missed_promised_callback | solution: callback_queue
- id: gate_code_sent_in_wrong_thread | bucket: Customer support | sub: Status chase texts | text: "The renter asks for the gate code again because I sent it in the wrong thread." | pain: repeated_access_question | solution: faq_replies
- id: did_the_other_side_sign | bucket: Customer support | sub: Status chase texts | text: "A client texts 'did the other side sign?' during a showing and I don't see it until I'm back in the car." | pain: mid_showing_client_text | solution: eta_sms

### Reviews nobody asks for
- id: never_sent_the_google_link | bucket: Customer support | sub: Reviews nobody asks for | text: "I finished a 5-star job last week and never texted the Google review link." | pain: no_review_ask | solution: review_ask
- id: only_the_mad_reviews_show_up | bucket: Customer support | sub: Reviews nobody asks for | text: "The only reviews that show up are the mad ones, because happy people don't think to post." | pain: happy_customers_silent | solution: review_ask
- id: review_screenshot_still_unsent | bucket: Customer support | sub: Reviews nobody asks for | text: "I screenshot the review link after every walkthrough and still forget to send it." | pain: review_ask_forgotten | solution: review_ask
- id: closing_went_great_no_ask | bucket: Customer support | sub: Reviews nobody asks for | text: "The closing went great and I never asked them to review me on Google or Zillow." | pain: post_close_no_review | solution: review_ask

### Complaints I see too late
- id: angry_facebook_comment_two_days | bucket: Customer support | sub: Complaints I see too late | text: "An angry Facebook comment sat for two days because I don't check the Page inbox." | pain: unseen_public_complaint | solution: complaint_watch
- id: google_listing_message_then_star | bucket: Customer support | sub: Complaints I see too late | text: "They replied on my Google listing instead of calling, and I only saw it after the one-star." | pain: listing_message_missed | solution: inbox_unify
- id: damage_dispute_while_in_turo_chat | bucket: Customer support | sub: Complaints I see too late | text: "A guest's damage dispute sat in email while I was answering Turo chat." | pain: dispute_in_wrong_inbox | solution: inbox_unify
- id: inspection_objection_on_the_portal | bucket: Customer support | sub: Complaints I see too late | text: "The inspection objection landed in the brokerage portal and I was only watching my phone texts." | pain: portal_message_missed | solution: inbox_unify
