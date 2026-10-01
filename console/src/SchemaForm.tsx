import { isSecretRef, type JsonSchema } from "./api";

// Minimal JSON-schema form for provider settings (pydantic model schemas): strings, numbers, booleans,
// enums, secrets, and string maps (headers). Empty fields fall back to the provider's default.

function baseType(s: JsonSchema): { type: string; schema: JsonSchema } {
  if (s.anyOf) {
    const non = s.anyOf.find(x => x.type !== "null") ?? s.anyOf[0];
    return { type: String(non.type ?? "string"), schema: { ...non, description: s.description, default: s.default } };
  }
  return { type: String(Array.isArray(s.type) ? s.type[0] : s.type ?? "string"), schema: s };
}

const MASK = "••••••";

const shown = (x: unknown) => x == null || x === "" ? "" : typeof x === "object" ? JSON.stringify(x) : String(x);

export default function SchemaForm({ schema, value, onChange, effective, secretNames }: {
  schema: JsonSchema; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void;
  /** Values in use now (from .env when nothing is stored) — shown in empty fields. */
  effective?: Record<string, unknown>;
  /** Stored secret names: secret fields can reference one instead of taking a typed value. */
  secretNames?: string[];
}) {
  const props = schema.properties ?? {};
  const set = (k: string, v: unknown) => onChange({ ...value, [k]: v });
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {Object.entries(props).map(([name, raw]) => {
        const { type, schema: s } = baseType(raw);
        const secret = s.format === "password" || s.writeOnly || name === "api_key";
        const v = value[name];
        const eff = shown(effective?.[name]);
        const label = (
          <div className="mb-1 flex items-baseline justify-between gap-2">
            <span className="text-xs font-medium">{name}{schema.required?.includes(name) && <span className="text-bad"> *</span>}</span>
            {s.default !== undefined && typeof s.default !== "object" && <span className="truncate text-[10px] text-muted">default {String(s.default)}</span>}
          </div>
        );
        const help = s.description && <div className="mt-1 text-[11px] text-muted">{s.description}</div>;
        let input;
        if (s.enum) {
          input = (
            <select className="w-full" value={v == null ? "" : String(v)} onChange={e => set(name, e.target.value || undefined)}>
              <option value="">{eff ? `(default: ${eff})` : "(default)"}</option>
              {s.enum.map(o => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
            </select>
          );
        } else if (type === "boolean") {
          input = (
            <select className="w-full" value={v == null ? "" : String(v)} onChange={e => set(name, e.target.value === "" ? undefined : e.target.value === "true")}>
              <option value="">{eff ? `(default: ${eff})` : "(default)"}</option><option value="true">true</option><option value="false">false</option>
            </select>
          );
        } else if (type === "integer" || type === "number") {
          input = <input type="number" className="w-full" step={type === "integer" ? 1 : "any"} value={v == null ? "" : String(v)}
            placeholder={eff ? `${eff} (in use)` : ""}
            onChange={e => set(name, e.target.value === "" ? undefined : Number(e.target.value))} />;
        } else if (type === "object") {
          input = <textarea className="w-full font-mono text-xs" rows={2} placeholder={eff ? `${eff} (in use)` : '{"Authorization": "Bearer …"}'}
            value={v == null ? "" : typeof v === "string" ? v : JSON.stringify(v)}
            onChange={e => { try { set(name, e.target.value ? JSON.parse(e.target.value) : undefined); } catch { set(name, e.target.value); } }} />;
        } else if (secret && secretNames) {
          // a key is never shown: the field references a stored secret, or takes a new value (stored encrypted)
          const ref = isSecretRef(v) ? v.secret : "";
          input = (
            <div className="flex gap-2">
              <input type="password" className="min-w-0 flex-1" value={isSecretRef(v) || v == null ? "" : String(v)}
                placeholder={ref ? `stored as ${ref} — type to replace` : "paste a key (stored encrypted)"}
                onChange={e => set(name, e.target.value || (ref ? { secret: ref } : undefined))} />
              <select className="w-40" value={ref} onChange={e => set(name, e.target.value ? { secret: e.target.value } : undefined)}>
                <option value="">use a secret…</option>
                {secretNames.map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
          );
        } else {
          input = <input type={secret ? "password" : "text"} className="w-full" value={v == null ? "" : String(v)}
            placeholder={secret ? (v === MASK ? "stored" : eff === "set" ? "set in .env" : "from .env if empty")
              : eff ? `${eff} (from .env)` : ""}
            onChange={e => set(name, e.target.value || undefined)} />;
        }
        return <label key={name} className="block">{label}{input}{help}</label>;
      })}
    </div>
  );
}
