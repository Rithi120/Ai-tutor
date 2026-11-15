/* Learning resources as the page shows them: checked YouTube video cards and Wikimedia
 * pictures with their credit line (docs/LEARNING_RESOURCES.md). Shared by the Short Lesson
 * (app.js) and the chat (assistant.js).
 *
 * Everything here comes from the server, which built every link from validated ids, but it
 * is still treated as untrusted text: nodes are built with createElement and textContent,
 * and a link or picture whose address is not of the one expected shape is not shown at all.
 * Cards link out (no iframe, no YouTube script on the page); thumbnails come through the
 * app's own proxy, so the student's browser never contacts Google to show one.
 */
import { t } from "./i18n.js";

const WATCH_URL = /^https:\/\/www\.youtube\.com\/watch\?v=[A-Za-z0-9_-]{11}$/;
const THUMBNAIL_URL = /^\/media\/yt-thumb\/[A-Za-z0-9_-]{11}$/;
// The hosts media_enrichment.ALLOWED_IMAGE_HOSTS accepts and the CSP allows: Wikipedia's
// API gives scaled thumbnails on thumb.wikimedia.org (live 2026-11-14), originals on upload.
const IMAGE_URL = /^https:\/\/(?:upload|thumb)\.wikimedia\.org\//;
const CREDIT_URL = /^https:\/\/([a-z0-9-]+\.)*(wikimedia\.org|wikipedia\.org|creativecommons\.org)\//i;

/** "4:05", "1:02:03"; "" for an unknown length. */
export function formatDuration(seconds) {
  const total = Math.floor(Number(seconds) || 0);
  if (total <= 0) return "";
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

/** Whether a video card from the server has exactly the shape the page may show. */
export function isShowableVideo(video) {
  return Boolean(video && typeof video.title === "string" && video.title.trim()
    && WATCH_URL.test(String(video.url || "")) && THUMBNAIL_URL.test(String(video.thumbnail || "")));
}

/** Whether a picture carries the licence credit it must be shown with. */
export function isCreditedImage(image) {
  return Boolean(image && IMAGE_URL.test(String(image.url || "")) && image.licence
    && CREDIT_URL.test(String(image.file_url || "")));
}

function textNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text;
  return node;
}

function outLink(href, text, className = "") {
  const link = textNode("a", className, text);
  link.href = href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

/** One video card: thumbnail, title, channel · length, a "Supplementary" badge; the whole
 *  card opens the video on YouTube. A card whose thumbnail cannot load removes itself
 *  (the video may be gone) and `onGone` is told, so an empty box can go too. */
export function videoCardNode(video, { onGone } = {}) {
  if (!isShowableVideo(video)) return null;
  const card = outLink(video.url, "", "resource-video");
  card.dataset.videoId = String(video.video_id || "");
  const thumb = document.createElement("img");
  thumb.className = "resource-video-thumb";
  thumb.src = video.thumbnail;
  thumb.alt = "";
  thumb.loading = "lazy";
  thumb.decoding = "async";
  thumb.referrerPolicy = "no-referrer";
  thumb.width = 320;
  thumb.height = 180;
  thumb.addEventListener("error", () => {
    card.remove();
    onGone?.();
  }, { once: true });
  const text = document.createElement("span");
  text.className = "resource-video-text";
  text.appendChild(textNode("strong", "resource-video-title", video.title));
  const duration = formatDuration(video.duration_seconds);
  const meta = [String(video.channel || "").trim(), duration].filter(Boolean).join(" · ");
  if (meta) text.appendChild(textNode("span", "resource-video-meta", meta));
  const badges = document.createElement("span");
  badges.className = "resource-video-badges";
  badges.append(textNode("span", "resource-badge", t("resourcesBadge")),
    textNode("span", "resource-video-source", `▶ ${t("resourcesOpensYouTube")}`));
  text.appendChild(badges);
  card.append(thumb, text);
  return card;
}

/** The credit line a CC BY / CC BY-SA picture needs: author (linked to the file page),
 *  licence (linked to its deed when there is one) and where it is from. */
export function imageCreditNode(image) {
  const credit = document.createElement("span");
  credit.className = "resource-credit";
  const author = String(image.author || "").trim();
  const label = author ? t("resourcesImageBy", { author }) : t("resourcesImage");
  credit.appendChild(outLink(image.file_url, label));
  credit.appendChild(document.createTextNode(", "));
  const licenceUrl = String(image.licence_url || "");
  credit.appendChild(CREDIT_URL.test(licenceUrl) ? outLink(licenceUrl, image.licence) : document.createTextNode(image.licence));
  if (image.source) credit.appendChild(document.createTextNode(` (${image.source})`));
  return credit;
}

/** A credited picture: the image, what it shows, and the credit line. Null for a picture
 *  without a checked licence (lessons stored before the licence check). */
export function imageFigureNode(image, { className = "resource-image", onGone } = {}) {
  if (!isCreditedImage(image)) return null;
  const figure = document.createElement("figure");
  figure.className = className;
  const img = document.createElement("img");
  img.src = image.url;
  img.alt = String(image.alt || image.title || "");
  img.loading = "lazy";
  img.decoding = "async";
  img.referrerPolicy = "no-referrer";
  img.addEventListener("error", () => {
    figure.remove();
    onGone?.();
  }, { once: true });
  const caption = document.createElement("figcaption");
  const what = String(image.alt || image.title || "").trim();
  if (what) caption.append(textNode("span", "resource-image-what", what), document.createTextNode(" — "));
  caption.appendChild(imageCreditNode(image));
  figure.append(img, caption);
  return figure;
}
