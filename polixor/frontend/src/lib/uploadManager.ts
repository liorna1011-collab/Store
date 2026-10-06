// Uploads live here, not in a page: navigating away from New Project (to Projects, Settings,
// another project) never stops an upload, and the upload tray shows them everywhere.
//
// Start can be pressed before an upload has finished: the project's settings are kept with the
// upload and the project is created the moment the server has verified the file – the person
// can leave the page (but not close the tab: the bytes come from this browser).

import { useSyncExternalStore } from 'react'
import { api } from './api'
import { call, ResumableUpload, type UploadState } from './upload'
import { DirectUpload } from './directUpload'

interface Uploader {
  state: UploadState
  start(): Promise<UploadState['result']>
  pause(): void
  resume(): Promise<UploadState['result']>
  cancel(): Promise<void>
}

type CreateBody = Parameters<typeof api.createProject>[0]

export interface ManagedUpload {
  id: string
  name: string
  size: number
  state: UploadState
  /** settings for the project to create when the upload is verified (Start pressed early) */
  autoStart: Omit<CreateBody, 'source'> | null
  projectId: string | null
  projectError: string | null
  dismissed: boolean
}

let seq = 0
const items = new Map<string, ManagedUpload>()
const uploaders = new Map<string, Uploader>()
const subs = new Set<() => void>()
let snapshot: ManagedUpload[] = []

function publish() {
  snapshot = [...items.values()]
  subs.forEach((f) => f())
}

function patch(id: string, p: Partial<ManagedUpload>) {
  const cur = items.get(id)
  if (!cur) return
  items.set(id, { ...cur, ...p })
  publish()
}

async function createProject(id: string) {
  const it = items.get(id)
  if (!it || !it.autoStart || it.projectId || it.state.phase !== 'complete' || !it.state.result) return
  try {
    const project = await api.createProject({
      ...it.autoStart,
      source: { type: 'upload', upload_id: it.state.uploadId, upload_token: it.state.result.upload_token },
    })
    patch(id, { projectId: project.id, projectError: null })
    window.dispatchEvent(new CustomEvent('polixor:upload-project', { detail: { id, projectId: project.id } }))
  } catch (e) {
    patch(id, { projectError: e instanceof Error ? e.message : String(e) })
  }
}

export const uploadManager = {
  start(file: File): string {
    const id = `u${++seq}-${Date.now().toString(36)}`
    const onChange = (state: UploadState) => {
      patch(id, { state })
      if (state.phase === 'complete') void createProject(id)
    }
    // production (object storage configured): straight to the bucket; otherwise through Polixor
    const placeholder = new ResumableUpload(file, onChange)
    items.set(id, { id, name: file.name, size: file.size, state: placeholder.state, autoStart: null, projectId: null,
                    projectError: null, dismissed: false })
    publish()
    void call<{ storage?: string }>('GET', '/api/uploads/transport').catch(() => ({ storage: 'local_resumable' }))
      .then((t) => {
        if (!items.has(id)) return
        const u: Uploader = t.storage === 's3_multipart' ? new DirectUpload(file, onChange) : placeholder
        uploaders.set(id, u)
        void u.start()
      })
    return id
  },
  pause(id: string) { uploaders.get(id)?.pause() },
  resume(id: string) { void uploaders.get(id)?.resume() },
  async cancel(id: string) {
    await uploaders.get(id)?.cancel()
    uploaders.delete(id)
    items.delete(id)
    publish()
  },
  dismiss(id: string) { patch(id, { dismissed: true }) },
  /** Start pressed while the file is still uploading: the project starts by itself when it is verified. */
  startWhenReady(id: string, body: Omit<CreateBody, 'source'>) {
    patch(id, { autoStart: body })
    void createProject(id)
  },
  retryProject(id: string) { void createProject(id) },
  get(id: string): ManagedUpload | undefined { return items.get(id) },
  /** The upload a New Project page should show again (one not yet turned into a project). */
  current(): ManagedUpload | undefined {
    return [...items.values()].reverse().find((x) => !x.projectId && !x.autoStart && x.state.phase !== 'failed')
  },
  subscribe(f: () => void) { subs.add(f); return () => { subs.delete(f) } },
  snapshot: () => snapshot,
}

export function useUploads(): ManagedUpload[] {
  return useSyncExternalStore(uploadManager.subscribe, uploadManager.snapshot)
}

export function useManagedUpload(id: string | null): ManagedUpload | undefined {
  const all = useUploads()
  return id ? all.find((x) => x.id === id) : undefined
}
