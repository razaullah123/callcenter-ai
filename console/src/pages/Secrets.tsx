import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type SecretRow } from "../api";
import { Badge, Button, Card, Empty, ErrorBox, fmtTime } from "../ui";

export default function Secrets() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["secrets"], queryFn: api.secrets });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["secrets"] });
    qc.invalidateQueries({ queryKey: ["connections"] });
  };
  if (!q.data) return <ErrorBox error={q.error} />;
  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Secrets</h1>
        <p className="max-w-2xl text-sm text-muted">
          API keys and tokens, stored encrypted and referenced by name from connections and MCP servers.
          Values are never shown again after saving; the hint helps you recognise which key is stored.
        </p>
      </div>
      {!q.data.encryption && (
        <div className="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">
          MASTER_KEY is not set in .env, so secrets can't be stored. Add one (see .env.example) and restart the server.
        </div>
      )}
      {q.data.missing.length > 0 && (
        <div className="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">
          Referenced but not stored (read from .env for now):{" "}
          {q.data.missing.map(m => <span key={m.name} className="font-mono">{m.name} </span>)}
        </div>
      )}

      <Card title="Stored secrets">
        {q.data.secrets.length === 0 ? <Empty>No secrets stored yet.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-muted">
                  <th className="py-2 pr-3 font-medium">Name</th><th className="py-2 pr-3 font-medium">Value</th>
                  <th className="py-2 pr-3 font-medium">Used by</th><th className="py-2 pr-3 font-medium">Updated</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {q.data.secrets.map(s => <SecretLine key={s.name} s={s} onChanged={refresh} />)}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {q.data.encryption && <AddSecret onDone={refresh} />}
    </div>
  );
}

function SecretLine({ s, onChanged }: { s: SecretRow; onChanged: () => void }) {
  const [rotating, setRotating] = useState(false);
  const [value, setValue] = useState("");
  const [confirm, setConfirm] = useState(false);
  const put = useMutation({
    mutationFn: () => api.putSecret(s.name, value),
    onSuccess: () => { setRotating(false); setValue(""); onChanged(); },
  });
  const del = useMutation({ mutationFn: () => api.deleteSecret(s.name), onSuccess: onChanged });
  return (
    <>
      <tr className="border-b border-line last:border-0 align-top">
        <td className="py-2 pr-3 font-mono text-xs">{s.name}</td>
        <td className="py-2 pr-3 font-mono text-xs text-muted">{s.hint}</td>
        <td className="py-2 pr-3 text-xs">{s.used_by.length ? s.used_by.map(u => <div key={u}>{u}</div>) : <Badge>unused</Badge>}</td>
        <td className="py-2 pr-3 text-xs text-muted">{fmtTime(s.updated_at)}{s.updated_by ? ` · ${s.updated_by}` : ""}</td>
        <td className="py-2 text-right whitespace-nowrap">
          <Button onClick={() => setRotating(r => !r)}>{rotating ? "Cancel" : "Replace"}</Button>{" "}
          {confirm
            ? <><Button kind="danger" onClick={() => del.mutate()}>Delete</Button> <Button kind="ghost" onClick={() => setConfirm(false)}>Keep</Button></>
            : <Button kind="ghost" onClick={() => setConfirm(true)} disabled={s.used_by.length > 0}
                title={s.used_by.length ? "Still referenced" : undefined}>Delete</Button>}
        </td>
      </tr>
      {(rotating || put.error || del.error) && (
        <tr><td colSpan={5} className="pb-3">
          {rotating && (
            <form className="flex flex-wrap gap-2" onSubmit={e => { e.preventDefault(); put.mutate(); }}>
              <input id={`rotate-${s.name}`} type="password" className="min-w-0 flex-1" autoComplete="off"
                placeholder={`New value for ${s.name}`} value={value} onChange={e => setValue(e.target.value)} />
              <Button kind="primary" type="submit" disabled={!value || put.isPending}>{put.isPending ? "Saving…" : "Save new value"}</Button>
            </form>
          )}
          <ErrorBox error={put.error ?? del.error} />
        </td></tr>
      )}
    </>
  );
}

function AddSecret({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const put = useMutation({
    mutationFn: () => api.putSecret(name.trim().toUpperCase(), value),
    onSuccess: () => { setName(""); setValue(""); onDone(); },
  });
  return (
    <Card title="Add a secret">
      <form className="flex flex-wrap gap-2" onSubmit={e => { e.preventDefault(); put.mutate(); }}>
        <input id="new-secret-name" className="w-64 font-mono" placeholder="NAME_IN_CAPITALS" value={name}
          onChange={e => setName(e.target.value)} />
        <input id="new-secret-value" type="password" className="min-w-0 flex-1" autoComplete="off" placeholder="Value"
          value={value} onChange={e => setValue(e.target.value)} />
        <Button kind="primary" type="submit" disabled={!name.trim() || !value || put.isPending}>
          {put.isPending ? "Saving…" : "Save secret"}
        </Button>
      </form>
      <ErrorBox error={put.error} />
    </Card>
  );
}
