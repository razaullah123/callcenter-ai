import { cx } from "./ui";

// The Cloud Solutions logo (public/cloud_solutions_logo.png, 262×270, transparent). Like Hamsa: the full logo where
// there is room, the mark alone (the red cloud, the top of the image) in the collapsed sidebar.
export const LOGO_URL = `${import.meta.env.BASE_URL}cloud_solutions_logo.png`;

/** The full logo; on a dark theme it sits on a white tile so its dark lettering stays readable. */
export function Logo({ className }: { className?: string }) {
  return <img src={LOGO_URL} alt="Cloud Solutions" draggable={false}
    className={cx("shrink-0 object-contain dark:rounded-md dark:bg-white dark:p-0.5", className)} />;
}

/** Just the cloud: the image cropped to its top-left part (≈190×150 of 262×270). */
export function LogoMark() {
  return (
    <span className="relative block h-[26px] w-8 shrink-0 overflow-hidden" role="img" aria-label="Cloud Solutions">
      <img src={LOGO_URL} alt="" draggable={false} className="absolute top-0 left-0 w-[44px] max-w-none" />
    </span>
  );
}
