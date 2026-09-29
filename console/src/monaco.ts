// Bundle Monaco locally (no CDN) so the console works on a closed network.
import { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";

(self as unknown as { MonacoEnvironment: unknown }).MonacoEnvironment = { getWorker: () => new EditorWorker() };
loader.config({ monaco });

export const editorTheme = () => (matchMedia("(prefers-color-scheme: dark)").matches ? "vs-dark" : "vs");
