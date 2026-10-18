#!/usr/bin/env bash
# Drive a queue with any posting command. Replace `your_post_command` with the
# thing that actually posts (an API call, a browser helper, ...). It must print
# the new post's URL or id on success.
#
#   postgate queue load plan.txt
#   ./post_loop.sh
set -u
PG="${PG:-postgate}"

while true; do
  out=$($PG --json queue next); rc=$?
  case $rc in
    0)  ;;                                            # clear to post ONCE
    2)  sleep "$(echo "$out" | python3 -c 'import json,sys;print(json.load(sys.stdin)["wait_seconds"])')"
        continue ;;
    10) echo "queue finished"; exit 0 ;;
    *)  echo "postgate says stop (rc=$rc): $out"; exit "$rc" ;;
  esac

  text=$(echo "$out"   | python3 -c 'import json,sys;print(json.load(sys.stdin)["text"])')
  parent=$(echo "$out" | python3 -c 'import json,sys;print(json.load(sys.stdin)["parent"] or "")')

  if ref=$(your_post_command "$text" "$parent"); then
    $PG queue done "$ref" || exit $?
  else
    # The poster says it failed. It may have posted anyway: never retry blind.
    if $PG lookup --text "$text"; then
      echo "it went out after all; record the URL above with: $PG queue done <url>"
    else
      echo "not found; at most one retry is allowed (run this loop again)."
    fi
    exit 1
  fi
done
