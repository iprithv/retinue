/** Data takeout & import (§18): leaving must be as easy as arriving. */
import { DatabaseBackup, Download, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { Button } from "../../components/ui";
import { useAuth } from "../../stores/auth";

export function DataSection() {
  const importRef = useRef<HTMLInputElement | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const exportAll = () => {
    const token = useAuth.getState().accessToken;
    void fetch("/api/export", {
      headers: token ? { authorization: `Bearer ${token}` } : {},
    })
      .then((r) => r.blob())
      .then((blob) => {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "retinue-export.zip";
        a.click();
        URL.revokeObjectURL(a.href);
      });
  };

  const importAll = async (file: File) => {
    setStatus("importing…");
    const token = useAuth.getState().accessToken;
    const form = new FormData();
    form.append("file", file);
    const response = await fetch("/api/import", {
      method: "POST",
      headers: token ? { authorization: `Bearer ${token}` } : {},
      body: form,
    });
    if (!response.ok) {
      setStatus("import failed — is this a Retinue export archive?");
      return;
    }
    const counts = (await response.json()) as Record<string, number>;
    setStatus(
      `imported ${counts.conversations} conversations, ${counts.agents} agents, ${counts.memories} memories`,
    );
  };

  return (
    <section className="space-y-4">
      <h2 className="flex items-center gap-2 text-lg font-semibold">
        <DatabaseBackup className="size-4" /> Your data
      </h2>
      <p className="text-sm text-muted">
        Export everything — conversations, agents, memories — as a JSONL zip, or import a
        previous export.
      </p>
      <div className="flex gap-2">
        <Button variant="outline" onClick={exportAll}>
          <Download className="size-3.5" /> Export all
        </Button>
        <Button variant="outline" onClick={() => importRef.current?.click()}>
          <Upload className="size-3.5" /> Import
        </Button>
        <input
          ref={importRef}
          type="file"
          accept=".zip"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void importAll(file);
            e.target.value = "";
          }}
        />
      </div>
      {status ? <div className="text-xs text-muted">{status}</div> : null}
    </section>
  );
}
