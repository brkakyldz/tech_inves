import { revalidateTag } from "next/cache";
import { NextRequest, NextResponse } from "next/server";

/**
 * Called by `RunControlPanel` (`components/runs/RunControlPanel.tsx`) the
 * moment a triggered run reaches `succeeded`, so the cached pages pick up
 * the new scores/reports well before `FALLBACK_REVALIDATE_SECONDS`
 * (`lib/api/client.ts`) would otherwise expire them. That is a same-origin,
 * unauthenticated caller, matching every other trigger endpoint in this
 * on-demand, single-user tool (`src/techinves/api/routers/runs.py` carries
 * no auth either -- ADR 0010 §1). A failed call here is non-fatal to the
 * run that triggered it -- the data is already correct in the database,
 * this only busts the cache early instead of waiting for the fallback TTL.
 *
 * `REVALIDATE_SECRET` is optional and off by default (no caller sets it):
 * if a deployment configures it, this route re-gates on it for any future
 * external caller. Unset, as in every clone of this personal-use tool, the
 * internal caller above works with no extra plumbing.
 */

// `meta` belongs here because `lib/data/meta.ts` caches `GET /v1/meta`
// under that tag with the same one-hour fallback floor as everything else.
// Without it the tag was uninvalidatable: the site-wide `DemoModeBanner`
// could keep asserting "no API keys are configured, the triggers are
// disabled" for up to an hour after a run proved otherwise, while
// `RunControlPanel` -- which reads the very same endpoint with
// `cache: "no-store"` -- showed those triggers enabled right next to it.
const VALID_TAGS = new Set(["scores", "reports", "meta", "all"]);

export async function POST(request: NextRequest) {
  const expectedSecret = process.env.REVALIDATE_SECRET;
  if (expectedSecret) {
    const secret = request.headers.get("x-revalidate-secret");
    if (secret !== expectedSecret) {
      return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    }
  }

  const tag = request.nextUrl.searchParams.get("tag");
  if (!tag || !VALID_TAGS.has(tag)) {
    return NextResponse.json({ error: `invalid tag: ${tag}` }, { status: 400 });
  }

  const tagsToRevalidate = tag === "all" ? ["scores", "reports", "meta"] : [tag];
  for (const t of tagsToRevalidate) {
    // Immediate expiration, not stale-while-revalidate: an external webhook
    // (the ingestion job) is telling us data already changed in Postgres,
    // so the next request should fetch fresh rather than serve one more
    // stale response. See the revalidateTag docs' "webhooks" note.
    revalidateTag(t, { expire: 0 });
  }

  return NextResponse.json({ revalidated: tagsToRevalidate, now: Date.now() });
}
