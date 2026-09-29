More free Gemini quota = more AI Studio projects (quota is per project). Drives Dia (CDP :9223) in its own background window, account = whoever is signed into AI Studio in Dia.

    python3 mkproj.py swiper-7 && python3 finishkey.py swiper-7     # new project + key -> keychain `gemini-keys`
    cd ../../worker && security find-generic-password -s gemini-keys -w | tr -d '\n' | wrangler secret put GEMINI_KEYS
