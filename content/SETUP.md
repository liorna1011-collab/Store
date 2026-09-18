# Sideglass — Shopify setup guide

Everything here assumes a brand-new Shopify store. Work top to bottom; each step depends on the one above it.

> **What I could and could not verify.** I built and validated this theme against Shopify's official `@shopify/theme-check` (v3.29) — **0 errors**. But I have no Shopify account, so I could not upload it, click through it, or place a test order. The first real run is yours. Step 9 is the click-through checklist.

---

## 0. Before you touch Shopify

Two things must be true first, or you will build a shop you cannot ship from:

- [ ] **You have samples in your hands.** Do not photograph, describe or price a product you have not held.
- [ ] **You have decided the brand name and checked the trademark.** Renaming a live store means redoing the domain, the emails, the packaging and the ad accounts.

If either is missing, stop and go back to the research report's First 10 Actions.

---

## 1. Create the store

1. Sign up at shopify.com — **Basic plan, $39/mo**. Do not start on a higher plan.
2. Settings → **Store details**
   - Store name: `Sideglass`
   - Store currency: **GBP** (the research recommends UK-first)
   - Unit system: Metric
   - Time zone: London
3. Settings → **Payments** → enable **Shopify Payments**
   - Then enable PayPal, Apple Pay, Google Pay.
   - Klarna or Clearpay is optional; it lifts AOV on a £79 order but costs ~3–6%.
4. Settings → **Checkout**
   - Customer accounts: **Optional** (forcing accounts kills conversion)
   - Enable **Abandoned checkout emails**, delay 10 hours
   - Tipping: **off** (wrong signal for a technical brand)

---

## 2. Upload the theme

**Option A — the simple way**

1. Zip the `theme/` folder so that `layout/`, `sections/`, `templates/` etc. sit at the **root of the zip**, not inside a `theme/` folder.
   ```bash
   cd theme && zip -r ../sideglass-theme.zip . -x ".*" && cd ..
   ```
2. Shopify admin → **Online Store → Themes → Add theme → Upload zip file**.
3. Once uploaded, click **Customize** to check it renders, then **Publish**.

**Option B — the developer way (better if you will keep editing)**

```bash
npm install -g @shopify/cli
cd theme
shopify theme dev --store your-store.myshopify.com   # live local preview
shopify theme push                                    # upload when happy
```

---

## 3. Create the products

The theme is built around **one hero product with three bundle variants**, plus two standalone products. This matters: the bundle picker on the product page only appears when the hero product has more than one variant.

### Product 1 — the hero

- **Title:** `P4 Pressure Tube`
- **Handle:** `p4-pressure-tube` (the homepage links to this exact handle)
- **Option name:** `Kit`
- **Variants, in this exact order** — the theme pre-selects position 2:

  | # | Variant | Price | Compare-at | SKU |
  |---|---|---|---|---|
  | 1 | Tube only | £59.00 | — | SG-P4-01 |
  | 2 | **Match Ready Kit** | £79.00 | £93.00 | SG-P4-KIT |
  | 3 | Doubles Pack | £129.00 | £158.00 | SG-P4-DBL |

  > Only set a compare-at price where the saving is **real** — the sum of buying the parts separately. An invented "was" price is illegal to display in the UK.

- Weight: `310 g` (Tube only). Set each variant's real weight — shipping rates depend on it.
- Inventory: **Track quantity**, and **do not** continue selling when out of stock.
- Description: paste from `content/products.md`.

### Product 2

- **Title:** `Tack Overgrip — 12 pack` · handle `tack-overgrip` · **£22.00** · SKU `SG-GRIP-12`

### Product 3

- **Title:** `Edge Guard` · handle `edge-guard` · **£12.00** · SKU `SG-EDGE-01`

### Optional: per-variant sub-labels

The bundle picker shows a small grey line under each variant name if you add a **variant metafield**:

- Settings → Custom data → Variants → Add definition
- Namespace and key: `custom.unit_note`, type **Single line text**
- Example values: `Just the tube` · `Tube + 3 overgrips + cloth` · `Two tubes + 6 overgrips`

Same for product cards: a product metafield `custom.card_line` sets the one-line description under each card.

---

## 4. Collections

Create a **manual** collection:

- Title: `All` · handle `all`
- Add all three products, ordered: P4 Pressure Tube, Tack Overgrip, Edge Guard.

Then Online Store → Themes → Customize → home page → **The range** section → pick this collection.

---

## 5. Pages

Create these under Online Store → Pages. Copy is in `content/pages.md`.

| Title | Handle | Template |
|---|---|---|
| Why pressure matters | `why-pressure-matters` | `page` |
| About | `about` | `page` |
| FAQ | `faq` | **`page.faq`** ← select this, not `page` |
| Contact | `contact` | `page.contact` (Shopify's built-in) |
| Shipping | `shipping` | `page` |
| Returns & guarantee | `returns` | `page` |

> The FAQ page uses the custom `page.faq` template, which renders accordions from the theme rather than from the page body. Pick it in the page's **Theme template** dropdown on the right.

---

## 6. Navigation

Online Store → Navigation.

**Main menu**
- Shop → `/collections/all`
- The P4 Tube → `/products/p4-pressure-tube`
- Why pressure matters → `/pages/why-pressure-matters`
- FAQ → `/pages/faq`

**Footer menu** (create two: `Shop` and `Help`)
- *Shop:* P4 Pressure Tube · Tack Overgrip · Edge Guard
- *Help:* FAQ · Shipping · Returns & guarantee · Contact · Privacy policy · Terms of service

---

## 7. Theme settings

Customize → **Theme settings**:

- **Brand** → upload logo (or leave blank to use the wordmark) and favicon.
- **Colours** → accent `#0C8A76`, ink `#121A18`, ground `#F6F7F5`. Every other tone in the theme is derived from these three, so changing the accent restyles the whole store coherently.
- **Cart** → free shipping threshold `60`.
  > ⚠️ **Change it in two places.** Also edit `FREE_SHIPPING_THRESHOLD` at the top of `assets/theme.js` (it is in **pence**: `6000`). The progress bar in the cart drawer reads the JS value, not the setting. Shopify themes cannot read settings from a static JS asset.
- **Business details** → registered name, company number, trading address, support email. These print in the footer. **Do not skip these** — buyers look for them, and UK/EU consumer law expects a real address and contact route.

---

## 8. Shipping and tax

Settings → Shipping and delivery:

- **UK**: £3.95 standard tracked · **free over £60**
- **EU**: £7.95, **DDP (duties prepaid)**
- Rest of world: off for now

Settings → Taxes:
- Register for UK VAT when you cross the threshold, then enter the number. Prices in this theme are shown **VAT-inclusive**, which is the UK norm.
- **EU orders:** since 1 July 2026 the €150 duty exemption is gone. Register for **IOSS** and ship DDP, or your customers get surprise bills at the door and your refund rate goes through the roof.

---

## 9. Click-through checklist before you spend a penny on ads

Do all of this on a **phone**, not a laptop. Most of your traffic is mobile, and Shopify's average mobile conversion rate is 1.2% against 2.8% on desktop.

- [ ] Home page loads, hero reads correctly, no placeholder boxes left visible
- [ ] Product page: the bundle picker shows three options, **Match Ready Kit pre-selected**
- [ ] Changing the variant updates the price, the "Save" line and the button
- [ ] Add to bag → the cart drawer slides open with the right item
- [ ] Free-shipping progress bar shows the right remaining amount
- [ ] Remove an item → drawer updates without a page reload
- [ ] Checkout completes with a **real test order** (Shopify Payments test mode, then one real £1 order you refund)
- [ ] Order confirmation email arrives and looks right
- [ ] Every footer link works — especially the policies
- [ ] 404 page works (`/pages/does-not-exist`)
- [ ] Search works
- [ ] Meta Pixel and TikTok Pixel fire a **Purchase** event on the test order — verify in Events Manager, not just in the app settings

That last one is the one people skip and then wonder why their ads never optimise.

---

## 10. Apps — install these and nothing else

Every app costs money and page speed. Four is enough:

| Job | Suggestion | Why |
|---|---|---|
| Reviews | Judge.me (free tier) | Photo reviews; the free tier is genuinely enough to start |
| Email/SMS | Klaviyo (free to 250 contacts) | The day-40 overgrip reminder is where your profit lives |
| Post-purchase upsell | Shopify's own post-purchase extension, or Zipify OCU | Adds the Edge Guard at zero extra CAC |
| Pixels | Meta and TikTok official channels | Never a third-party pixel manager |

**Do not install:** countdown timers, fake "X people are viewing", spin-to-win popups, sticky discount bars. You are positioning against unreliability. Every one of those signals unreliability — and fake urgency is now actionable under the UK's DMCC Act 2024.

---

## Known trade-offs in this build

Being straight with you about the choices I made:

1. **Google Fonts, not Shopify-hosted fonts.** `theme-check` flags this as a performance warning (6 warnings, no errors). I kept it because Archivo + Newsreader + JetBrains Mono is the brand, and Shopify's font library does not carry all three. Mitigated with `preconnect` and `display=swap`. If speed becomes a problem, swap to `font_picker` settings and accept a plainer type pairing.
2. **The free-shipping threshold is defined twice** (theme setting + `theme.js`). Shopify cannot inject settings into a static JS asset. Documented in step 7.
3. **No reviews are included.** The reviews section deliberately renders a build-note instead of placeholder testimonials. Seed real ones before launch.
4. **Bundle = variants, not a bundle app.** Simpler, faster, no app fee — but it means the Kit's contents are described in copy rather than tracked as separate inventory. If you need component-level stock control later, move to Shopify Bundles.
5. **Single-theme (light) design.** A tool brand reads better on a clean ground and product photography stays predictable.

---

## File map

```
theme/
├── assets/theme.css          design system; all colour via CSS variables
├── assets/theme.js           cart drawer, bundle picker, accordions — no dependencies
├── config/                   theme settings schema and defaults
├── layout/theme.liquid       shell; colour tokens generated from settings here
├── layout/password.liquid    coming-soon page
├── locales/en.default.json   all UI strings
├── sections/                 19 sections, all editable in the theme editor
├── snippets/                 cart drawer, product card, trust row, placeholder
└── templates/                JSON templates incl. the custom page.faq

content/
├── SETUP.md      this file
├── products.md   product copy, ready to paste
├── pages.md      page copy, ready to paste
├── policies.md   policy drafts — read the warning at the top
├── emails.md     the five flows that matter
└── images.md     the shot list
```
