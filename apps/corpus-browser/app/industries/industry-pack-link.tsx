"use client";

import type { MouseEvent, ReactNode } from "react";

type IndustryPackLinkProps = {
  children: ReactNode;
  className: string;
  packId: string;
};

export default function IndustryPackLink({ children, className, packId }: IndustryPackLinkProps) {
  function openPack(event: MouseEvent<HTMLAnchorElement>) {
    const pack = document.getElementById(packId);
    if (!(pack instanceof HTMLDetailsElement)) return;

    event.preventDefault();
    pack.open = true;
    window.history.replaceState(null, "", `#${packId}`);
    pack.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <a className={className} href={`#${packId}`} onClick={openPack}>
      {children}
    </a>
  );
}
