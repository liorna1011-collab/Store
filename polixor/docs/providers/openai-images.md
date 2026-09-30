# OpenAI Images — provider notes (Stage 11, AI Image Studio)

Documentation checked on 2026-09-30, while implementing
`backend/polixor/services/image_models.py`, `images.py` and `image_studio.py`.

Pages used:

- The OpenAI model pages: `gpt-image-2.5-sunburst`, `gpt-image-2`.
- The image generation guide and the GPT Image 2.5 prompting guide.
- The `images.edit` API reference.

developers.openai.com can't be fetched from this environment, so the pages
were read through search results that quote them. Re-check this list
against the live pages before relying on a limit.

## Corrections to the approved plan

| Plan said | Current documentation | What Polixor does |
|---|---|---|
| Use the strongest current image model | The newest API models are **`gpt-image-2.5-sunburst`** (best quality) and **`gpt-image-2.5-flare`** (faster), released 2026-09-08. Before them came `gpt-image-2` (2026-04-21). | Default model: `gpt-image-2.5-sunburst`. Flare, `gpt-image-2` and `gpt-image-1` can be picked in Settings. |
| Multi-turn editing through the Responses API image tool (`previous_response_id`) | The Responses API tool supports multi-turn editing, **but the 2.5 models aren't available in it**; their model page lists only `v1/images/generations` and `v1/images/edits`. | Polixor keeps the conversation itself. Each follow-up is an `images/edits` call on the conversation's current image, and the earlier instructions go in as context. This works the same with every image model. |
| `input_fidelity` for faithful edits | `gpt-image-2` and later ignore `input_fidelity`; they are high-fidelity by default. | Not sent. |
| DALL·E 2/3 as options | These are legacy models. DALL·E 3 can't edit images. | Hidden from the model picker. An existing setting keeps working, and the Studio hides attachments when a model can't edit. |

## What each model supports (the capability layer)

| | 2.5 Sunburst / Flare | gpt-image-2 | gpt-image-1 |
|---|---|---|---|
| Generate / edit | yes / yes | yes / yes | yes / yes |
| Input images per edit | up to 16 | up to 16 | up to 16 |
| Mask | yes | yes | yes |
| Quality | auto, low, medium, high, xhigh, max | auto, low, medium, high | auto, low, medium, high |
| Size | custom; each side a multiple of 16; longest side ≤ 3840; aspect ratio between 1:3 and 3:1; 655,360–8,294,400 px | custom; multiple of 16; ≤ 3840 | 1024², 1536×1024, 1024×1536 |
| Transparent background | yes (png or webp) | preview | yes |

Sizes Polixor sends, chosen for video (at least 1080 px on the short side):

- 16:9 → 2048×1152
- 9:16 → 1152×2048
- 1:1 → 1536×1536

gpt-image-1 uses its three fixed sizes.

Quality comes from *Settings → AI images*. If the chosen model doesn't
support that level, Polixor uses the closest level below it, so it never
spends more than you asked for.

## How the Studio calls the API

- **New image:** `POST /v1/images/generations`, JSON: `model`, `prompt`,
  `n=1`, `size`, `quality`, `output_format=png`, and `background` only when
  it's not automatic.
- **Edit, or references:** `POST /v1/images/edits`, multipart. Each input
  image is a separate `image[]` part: the image being edited comes first,
  then the references. The other fields are the same as for a new image.
- Images come back as `b64_json`, are saved on the server and get a
  thumbnail.
- Temporary errors (429, 5xx, timeouts) are retried with backoff. A policy
  rejection, a bad prompt or a missing key is not retried. The error is
  shown in the conversation with a **Try again** button.

## Security

- The OpenAI key is stored encrypted on the server (`SecretStore`). Only
  the server sends it, in the `Authorization` header.
- No API response includes the key; tests check this.
- The browser never talks to api.openai.com; the UI test blocks it and
  checks.
- Uploaded references must be PNG, JPEG or WEBP, up to 20 MB and 60 MP.
  They are decoded and re-encoded to PNG, which strips metadata and
  anything that isn't image data, and are downscaled to 4096 px.
- Every Studio request that changes something needs the `X-Polixor-Request`
  header (CSRF protection).

## Not verified here

No real OpenAI key was used. `tests/test_image_studio.py` runs against a
mocked Images API, which checks every request: path, model, size, quality,
background and the number of input images. Before relying on the output,
make one real request with your key: create an image, edit it once, and add
it to a clip.
