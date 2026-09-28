#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: run-model.sh --from <claude|codex|pi> --model <slug> [--cwd DIR] [--write] [--dry-run] < prompt.md

Run one panel seat or delegate on a model slug from a pstack skill, using whichever
agent CLIs this machine has. Prints JSON: {"route", "how", "command"}, then the
model's answer when it ran one.

Team models: Claude runs at the effort the slug names (max by default). GPT runs on
gpt-6-sol at high effort in the standard service tier: a "-fast" suffix is dropped and
max or xhigh is lowered to high, because the fast tier and higher efforts cost more quota.

Routes:
  native    Your own agent runs this model. "how" says which subagent or model to use.
  external  Another installed CLI ran it (claude, codex, or pi). The answer follows the JSON.
  fallback  Nothing here reaches that model. Run the seat on your own model, keep the
            panel size, and say the panel was single-family. Exit code 3.

Options:
  --from      The agent you are running in.
  --model     A slug such as claude-opus-5-5-max, gpt-6-sol-high, inherit-parent, or auto.
  --cwd       Working directory for the external run (default: current directory).
  --write     Allow the external run to edit files. Default is read-only.
  --dry-run   Print the route and command without running anything.

Examples:
  run-model.sh --from claude --model gpt-6-sol-high < review-prompt.md
  run-model.sh --from codex --model claude-opus-5-5-max --dry-run < judge-prompt.md

Environment: TEAMKIT_CLAUDE_BIN, TEAMKIT_CODEX_BIN, TEAMKIT_PI_BIN override the CLI paths.
USAGE
}

die() {
  printf 'run-model: %s\nRun run-model.sh --help for usage.\n' "$1" >&2
  exit 2
}

json_escape() {
  local s=$1
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\t'/\\t}
  printf '%s' "$s"
}

emit() {
  printf '{"route":"%s","how":"%s","command":"%s"}\n' "$(json_escape "$1")" "$(json_escape "$2")" "$(json_escape "${3:-}")"
}

from="" model="" cwd=$PWD write=0 dry_run=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from) [ $# -ge 2 ] || die "--from needs a value"; from=$2; shift 2 ;;
    --model) [ $# -ge 2 ] || die "--model needs a value"; model=$2; shift 2 ;;
    --cwd) [ $# -ge 2 ] || die "--cwd needs a value"; cwd=$2; shift 2 ;;
    --write) write=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument $1" ;;
  esac
done
case "$from" in claude|codex|pi) ;; "") die "--from is required: claude, codex, or pi" ;; *) die "--from must be claude, codex, or pi, not \"$from\"" ;; esac
[ -n "$model" ] || die "--model is required, for example --model gpt-6-sol-high"

claude_bin=${TEAMKIT_CLAUDE_BIN:-claude}
codex_bin=${TEAMKIT_CODEX_BIN:-codex}
pi_bin=${TEAMKIT_PI_BIN:-pi}
have() { command -v "$1" >/dev/null 2>&1; }

if [ "$model" = "inherit-parent" ] || [ "$model" = "auto" ]; then
  emit native "Omit the model. Run this seat on your current agent's model."
  exit 0
fi

case "$model" in
  claude-*) family=claude ;;
  gpt-*) family=gpt ;;
  *) die "unknown model family for \"$model\"; expected claude-*, gpt-*, inherit-parent, or auto" ;;
esac

base=${model%-fast}
effort=
case "${base##*-}" in
  max|xhigh|high|medium|low|minimal) effort=${base##*-}; base=${base%-*} ;;
esac
if [ "$family" = gpt ]; then
  case "$effort" in ""|max|xhigh) effort=high ;; esac
else
  effort=${effort:-max}
fi

claude_alias() {
  case "$1" in
    claude-opus-*) printf opus ;;
    claude-sonnet-*) printf sonnet ;;
    claude-haiku-*) printf haiku ;;
    claude-fable-*) printf fable ;;
    *) printf '%s' "$1" ;;
  esac
}

if [ "$from" = claude ] && [ "$family" = claude ]; then
  emit native "Use the Agent tool with model: $(claude_alias "$base")."
  exit 0
fi
if [ "$from" = codex ] && [ "$family" = gpt ]; then
  emit native "Run it in Codex with model $base at reasoning effort $effort in the standard service tier (codex exec -m $base -c model_reasoning_effort=\"$effort\" -c service_tier=\"default\")."
  exit 0
fi
if [ "$from" = pi ] && [ "$family" = gpt ]; then
  emit native "Use the subagent tool with model openai-codex/$base and thinking $effort."
  exit 0
fi

prompt_file=$(mktemp)
answer_file=$(mktemp)
trap 'rm -f "$prompt_file" "$answer_file"' EXIT
if [ "$dry_run" -eq 0 ]; then cat > "$prompt_file"; fi

if [ "$family" = gpt ] && have "$codex_bin"; then
  sandbox=read-only; [ "$write" -eq 1 ] && sandbox=workspace-write
  cmd=("$codex_bin" exec -m "$base" -c "model_reasoning_effort=\"$effort\"" -c "service_tier=\"default\"" -s "$sandbox" --skip-git-repo-check -C "$cwd" -o "$answer_file" -)
elif [ "$family" = gpt ] && [ "$from" != pi ] && have "$pi_bin"; then
  cmd=("$pi_bin" -p --model "openai-codex/$base" --thinking "$effort")
elif [ "$family" = claude ] && have "$claude_bin"; then
  mode=plan; [ "$write" -eq 1 ] && mode=acceptEdits
  cmd=("$claude_bin" -p --model "$base" --effort "$effort" --permission-mode "$mode")
elif [ "$family" = claude ] && [ "$from" = pi ]; then
  emit native "If your Pi has the anthropic provider, use the subagent tool with model anthropic/$base and thinking $effort. If it does not, run this seat on your own model, keep the panel size, and say the panel was single-family."
  exit 0
else
  emit fallback "No installed CLI reaches $model. Run this seat on your own model, keep the panel size, and say the panel was single-family."
  exit 3
fi

printf -v shown '%q ' "${cmd[@]}"
emit external "Runs $model through ${cmd[0]##*/}." "${shown% }"
[ "$dry_run" -eq 1 ] && exit 0

if [ "${cmd[0]}" = "$pi_bin" ]; then
  (cd "$cwd" && "${cmd[@]}" "$(cat "$prompt_file")")
elif [ "${cmd[0]}" = "$codex_bin" ]; then
  "${cmd[@]}" < "$prompt_file" > /dev/null
  cat "$answer_file"
else
  (cd "$cwd" && "${cmd[@]}" < "$prompt_file")
fi
