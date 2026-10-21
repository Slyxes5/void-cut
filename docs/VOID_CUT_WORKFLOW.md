# Void Cut workflow

The workspace now has four views: `?view=source`, `?view=clips`, `?view=studio`, and `?view=exports`. Navigation uses browser history without reloading the application. Existing private `#clip=...` handoff fragments are preserved.

- Completed live analysis, cached analysis, history restore, and Telegram handoff import open the clip selection view.
- Clip selection includes an explicit **Open Studio** action. Studio retains its component state when navigating away, including unsaved titles and settings. Inactive previews and audio players are paused.
- Starting a render opens Exports. Existing server recovery, retry, verified download links, ZIP downloads, and social upload actions remain in the standalone results panel. Browser renders expose their in-memory blob downloads in the same view.
- Exports can be opened without a loaded analysis, including after a refresh. Server batch IDs are recovered through the existing recovery controller. Browser blob downloads are available only for the current document lifetime.
- Clip and Studio routes without an analysis return to Source. Browser refresh does not restore unsaved Studio edits; existing analysis history and saved preferences remain available. Back/Forward within the mounted workspace preserves the current selection and edits.

The warm theme is isolated in `src/workflow.css`. It changes UI surfaces and controls; subtitle timing, caption generation, and the render pipeline have not been redesigned. Internal storage names remain compatible with the existing application. The header uses the supplied Void Cut logo; the footer credits Cheat Clip PRO and links to the original repository. The Git remote and self-update mechanism have not been migrated.

The npm package, launchers, API title, application messages, and new browser download filenames now use Void Cut / `void-cut`. The physical checkout remains at `C:\Projects\cheat-clip-pro` while existing processes and integrations use it. Browser storage keys, session cookies, persisted ZIP filenames, and `%LOCALAPPDATA%/CheatClipPro` are deliberately retained so existing history, exports, encrypted social tokens, and Telegram credentials remain readable. These compatibility identifiers are not product branding. A future folder migration must account for external automation paths, Python virtual-environment entry points, and the saved project folder in the editor.

Validation: TypeScript/Vite production build; Node regression tests; browser checks with mocked APIs for automatic result navigation, selection and Studio state retention, Back/Forward, one visible workflow view, mobile/desktop overflow, a single render submission, and Exports recovery after reload. No paid model requests, live renders, or social uploads are used in the UI checks.
