import { DiffEditor } from "@monaco-editor/react";
import "../monaco";
import { editorTheme } from "../monaco";

export default function JsonDiff({ left, right }: { left: string; right: string }) {
  return <DiffEditor height="60vh" theme={editorTheme()} language="json" original={left} modified={right}
    options={{ readOnly: true, renderSideBySide: true, minimap: { enabled: false } }} />;
}
