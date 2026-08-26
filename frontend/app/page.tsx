import { HeroQuestionBar } from "@/components/HeroQuestionBar";
import { HomeActions } from "@/components/HomeActions";
import { HowItWorksButton } from "@/components/HowItWorksButton";

/**
 * Landing hub (#124). A real home, not a placeholder: a hero that opens
 * directly on the question bar (the standalone "TEKIJIN" title + product
 * description above it was dropped so the hub gets straight to the primary
 * action), plus role-oriented action cards. Every link points at an existing
 * route (#121). Server component — no client state of its own;
 * `HeroQuestionBar` (typed input, #392), `HomeActions` (needs the signed-in
 * principal's role to gate the admin-only dashboard card, #347), and
 * `HowItWorksButton` (#392) each own their own interactivity.
 *
 * `HowItWorksButton`'s popover follows the #292 product direction: implicit
 * knowledge is accumulated and converted into explicit knowledge over time,
 * so the answer source is not framed as "always a person" (#324) — it
 * describes self-answer as a growing future capability, not a live one,
 * since `self_answer_enabled` still defaults to off (#291 part3) and the
 * concrete flow today is still "AI forwards, a person answers."
 */
export default function HomePage() {
  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-lg py-lg">
      <section className="flex flex-col items-center gap-md py-lg text-center">
        <HeroQuestionBar />
      </section>

      <HomeActions />

      <HowItWorksButton />
    </div>
  );
}
