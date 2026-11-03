// @ts-check
import { apiFetch } from "./api-client.mjs";
import { toast, toastError } from "./toast.mjs";

export const NO_PICKER_MESSAGE =
  "There's no folder picker here (it needs a Mac): type the folder path into the box instead.";

/** For the Libraries buttons, which have no box to type into. */
export const NO_PICKER_LIBRARY_MESSAGE =
  "Adding a library needs a Mac's folder picker. On a server, start bpp with --library <folder> (in Docker, the /data folder is the library).";

/**
 * Open the server's native folder picker and return the chosen path,
 * or "" when the user cancels. Off macOS (the Docker image, a Linux
 * server) there is no picker, so say so instead of doing nothing.
 * @param {string} [noPickerMessage] what to say then
 * @returns {Promise<string>}
 */
export async function pickFolder(noPickerMessage = NO_PICKER_MESSAGE) {
  try {
    const data = await apiFetch("/api/v1/pick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "folder" }),
    });
    if (data.unsupported) toast(noPickerMessage, "warning");
    return data.path || "";
  } catch (e) {
    toastError("open the folder browser", e);
    return "";
  }
}
