---
name: insurance
description: Insurance details, insurance update request status, insurance approval status
keywords: [تامين, التامين, بطاقة التامين, موافقة التامين, موافقه, تحديث التامين, insurance, approval, insurance card, insurance update]
status: draft   # flow spec pending
---
Answer the caller's insurance question with the matching tool, then summarise the result in one or two sentences.
- Insurance details (company, class, validity) → api_get_insurance_detail
- Status of an insurance update request → api_get_Insurance_UpdateHistory
- Status of an insurance approval → api_get_insurance_approvals (read at most 3 approvals, latest first)
Say dates naturally. If nothing is found, say so and offer a transfer to the insurance team.
