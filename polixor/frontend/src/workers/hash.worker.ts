// SHA-256 of an upload chunk, off the page's main thread: reading 16-32 MB into memory
// and hashing it never stalls scrolling, typing or the progress bar.

const ctx = self as unknown as { postMessage: (m: unknown) => void; crypto: Crypto }

self.onmessage = async (e: MessageEvent<{ id: number; blob: Blob }>) => {
  const { id, blob } = e.data
  try {
    if (!ctx.crypto?.subtle) { ctx.postMessage({ id, hex: '' }); return }
    const buf = await ctx.crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
    let hex = ''
    const bytes = new Uint8Array(buf)
    for (let i = 0; i < bytes.length; i++) hex += bytes[i].toString(16).padStart(2, '0')
    ctx.postMessage({ id, hex })
  } catch {
    ctx.postMessage({ id, hex: '' })       // no checksum: the server still checks the size
  }
}
