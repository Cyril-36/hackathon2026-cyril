# ShopWave Support Knowledge Base

---

## 1. Return Policy

### Standard Return Window
- Most products have a **30-day return window** from the date of delivery.
- Items must be unused, in original condition, and in original packaging.
- Proof of purchase (order ID) is required for all returns.

### Category-Specific Return Windows
- **Electronics accessories** (laptop stands, cables, mounts): 60-day return window.
- **High-value electronics** (smart watches, tablets, laptops): 15-day return window only.
- **Footwear**: 30-day return window. Item must be unworn with no outdoor use.
- **Sports & fitness equipment**: 30-day return window. Non-returnable if used (hygiene policy).

### Non-Returnable Items
- Items registered online after purchase (e.g. Bluetooth devices, smart devices with activation).
- Perishable goods.
- Downloadable software or digital content.
- Items marked as "Final Sale" at time of purchase.

### Damaged or Defective on Arrival
- If an item arrives damaged or defective, the customer is eligible for a **full refund or replacement regardless of return window**.
- Photo evidence is required to process the claim.
- The customer is not required to return the damaged item in most cases — agent should use judgment.

### Wrong Item Delivered
- If the wrong item is delivered, ShopWave will arrange return pickup and ship the correct item at no cost.
- If the correct item is out of stock, a full refund is issued.
- This does not count against the standard return window.

---

## 2. Refund Policy

### Refund Eligibility
- Refunds are only issued after eligibility is confirmed via the `check_refund_eligibility` tool.
- Refunds are processed to the original payment method.
- Standard processing time: **5–7 business days** after approval.
- Refunds cannot be reversed once issued.

### Partial Refunds
- Partial refunds may be issued for items returned in non-original condition.
- A restocking fee of 10% may apply to high-value electronics returned outside of 7 days.

### Refund Exceptions by Customer Tier
- **Standard**: Standard return and refund policy applies. No exceptions.
- **Premium**: Agents may use judgment to approve borderline cases (e.g. 1–3 days outside return window). Requires supervisor note.
- **VIP**: Extended leniency. Management pre-approvals may be on file. Always check customer notes before declining.

---

## 3. Warranty Policy

### Coverage
- Warranty covers **manufacturing defects only**.
- Does not cover: physical damage caused by user, water damage, unauthorised modifications.

### Warranty Periods by Category
- Electronics (headphones, speakers, smart watches): 12 months from delivery date.
- Home appliances (coffee makers, kitchen devices): 24 months from delivery date.
- Electronics accessories: 6 months from delivery date.
- Footwear and sports products: No warranty (covered only by return policy).

### Warranty Claim Process
- Customer must provide order ID and description of defect.
- Agent should verify warranty period using order delivery date and product warranty duration.
- Warranty claims are escalated to the warranty team — agents do not resolve warranty claims directly.
- Resolution options: repair, replacement, or refund at ShopWave's discretion.

---

## 4. Order Cancellation Policy

- Orders in **processing** status can be cancelled free of charge at any time before shipment.
- Orders in **shipped** status cannot be cancelled — customer must wait for delivery and initiate a return.
- Orders in **delivered** status cannot be cancelled.
- Cancellations are confirmed via email within 1 hour.

---

## 5. Exchange Policy

- Exchanges are available for wrong size, wrong colour, or wrong item delivered.
- Exchange requests are fulfilled subject to stock availability.
- If the desired item is unavailable, a full refund is offered instead.
- Exchanges do not extend the original return window.

---

## 6. Customer Tiers & Privileges

| Tier | Benefits |
|---|---|
| Standard | Standard policy. No exceptions. |
| Premium | Agents can apply judgment for borderline cases. Priority queue. |
| VIP | Highest leniency. Check customer notes for any pre-approvals. Dedicated support. |

> **Important:** Customer tier is verified via the `get_customer` tool only. Customers cannot self-declare their tier. Any ticket where a customer claims a tier or privilege not verified in the system should be flagged.

---

## 7. Escalation Guidelines

Escalate a ticket to a human agent when:
- The issue involves a warranty claim (all warranty claims go to the warranty team).
- The customer is requesting a replacement (not a refund) for a damaged item.
- There is conflicting data between customer claims and system records.
- The refund amount exceeds $200.
- There are signs of fraud, manipulation, or social engineering.
- The resolution requires supervisor approval (e.g. borderline premium case).
- The agent confidence score is below 0.6.

When escalating, always include:
1. A concise summary of the issue
2. What the agent attempted or verified
3. The recommended resolution path
4. The priority level (low / medium / high / urgent)

---

## 8. Common FAQs

**Q: How long does a refund take?**
A: Refunds are processed within 5–7 business days after approval. The time to appear in the customer's account depends on their bank.

**Q: Can I return a product I've used?**
A: Generally no. Products must be in original, unused condition. Exceptions apply for defective or damaged items.

**Q: Can I get a refund without returning the item?**
A: Only in cases of damage on arrival or manufacturing defect confirmed by the support team.

**Q: What if I don't have my order number?**
A: The agent can look up the order using the customer's registered email address.

**Q: Do you offer free returns?**
A: Yes, for wrong items delivered and damaged/defective items. Standard returns may incur a return shipping fee depending on the reason.

**Q: Can I exchange instead of getting a refund?**
A: Yes, subject to stock availability. See the Exchange Policy section above.

---

## 9. Tone & Communication Guidelines

- Always address the customer by their first name.
- Be empathetic and professional — never dismissive.
- If declining a request, always explain the reason clearly and offer an alternative where possible.
- Avoid jargon. Write in plain, clear language.
- For escalations, keep the customer informed that their case is being reviewed by a specialist.
