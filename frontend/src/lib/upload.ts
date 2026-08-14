/** Upload engine (§11): path A direct multipart for small files, path B
 * resumable chunks for large ones — with BLAKE3 pre-hashing so an identical
 * file never uploads twice (the fastest upload is no upload). */
import { blake3 } from "hash-wasm";
import { refreshAccessToken, useAuth } from "../stores/auth";
import { ApiError, api } from "./api/client";
import type { FileInfo, UploadSessionInfo } from "./api/types";

const DIRECT_LIMIT = 18 * 1024 * 1024; // stay under the server's 20 MB path-A cap
const CHUNK_RETRIES = 3;

export interface UploadProgress {
  sent: number;
  total: number;
}

async function authHeaders(): Promise<Record<string, string>> {
  const token = useAuth.getState().accessToken;
  return token ? { authorization: `Bearer ${token}` } : {};
}

async function hashFile(file: File): Promise<string> {
  // hash-wasm's incremental API keeps memory flat on large files
  const { createBLAKE3 } = await import("hash-wasm");
  const hasher = await createBLAKE3();
  const reader = file.stream().getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    hasher.update(value);
  }
  return hasher.digest("hex");
}

async function uploadDirect(file: File, onProgress?: (p: UploadProgress) => void): Promise<FileInfo> {
  const form = new FormData();
  form.append("file", file, file.name);
  const headers = await authHeaders();
  const response = await fetch("/api/files/direct", { method: "POST", headers, body: form });
  if (response.status === 401) {
    const fresh = await refreshAccessToken();
    if (fresh) return uploadDirect(file, onProgress);
  }
  if (!response.ok) {
    const envelope = await response.json().catch(() => null);
    throw new ApiError(response.status, envelope?.error ?? null);
  }
  onProgress?.({ sent: file.size, total: file.size });
  return response.json() as Promise<FileInfo>;
}

async function patchChunk(
  uploadId: string,
  offset: number,
  chunk: Blob,
): Promise<void> {
  for (let attempt = 0; ; attempt++) {
    const headers = await authHeaders();
    const response = await fetch(`/api/uploads/${uploadId}`, {
      method: "PATCH",
      headers: {
        ...headers,
        "Upload-Offset": String(offset),
        "content-type": "application/offset+octet-stream",
      },
      body: chunk,
    });
    if (response.ok || response.status === 204) return;
    if (attempt >= CHUNK_RETRIES) {
      const envelope = await response.json().catch(() => null);
      throw new ApiError(response.status, envelope?.error ?? null);
    }
    await new Promise((r) => setTimeout(r, 300 * 2 ** attempt));
    // resync the offset after a failure (kill-and-resume, §11.3)
    const head = await fetch(`/api/uploads/${uploadId}`, {
      method: "HEAD",
      headers: await authHeaders(),
    });
    const serverOffset = Number(head.headers.get("Upload-Offset") ?? offset);
    if (serverOffset !== offset) return; // this chunk actually landed
  }
}

async function uploadResumable(
  file: File,
  digest: string,
  onProgress?: (p: UploadProgress) => void,
): Promise<FileInfo> {
  const session = await api<UploadSessionInfo>("/api/files", {
    method: "POST",
    body: { name: file.name, size: file.size, mime: file.type || null, blake3: digest },
  });
  if (session.already_exists || session.upload_id === null) {
    onProgress?.({ sent: file.size, total: file.size });
    return api<FileInfo>(`/api/files/${session.file_id}`);
  }
  let offset = 0;
  while (offset < file.size) {
    const chunk = file.slice(offset, offset + session.chunk_size);
    await patchChunk(session.upload_id, offset, chunk);
    offset += chunk.size;
    onProgress?.({ sent: offset, total: file.size });
  }
  return api<FileInfo>(`/api/uploads/${session.upload_id}/complete`, {
    method: "POST",
    body: { blake3: digest },
  });
}

export async function uploadFile(
  file: File,
  onProgress?: (p: UploadProgress) => void,
): Promise<FileInfo> {
  if (file.size <= DIRECT_LIMIT) return uploadDirect(file, onProgress);
  const digest = await hashFile(file);
  return uploadResumable(file, digest, onProgress);
}

export { blake3 };
