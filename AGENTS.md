# Product design context

We are designing the Buddy website and the mobile version of the app in this repository.
Keep the desktop and mobile experiences coherent, and treat touch input, safe areas, and on-screen keyboard behavior as part of the mobile design.

# Code readability

- Prefer straightforward code that can be read once over compact code that saves lines.
- For small, fixed sets of commands or actions, write explicit registration calls. Do not encode control flow in nested arrays of command/callback tuples and immediately loop over them.
- Extract repeated error handling and disposal into a named helper. Give substantial callbacks and configuration objects meaningful names before passing them to another function.
- Use ordinary conditionals instead of nested ternaries. Keep distinct operations on separate lines and use explicit object properties when mapping a small, fixed set of settings.
- Do not address a readability complaint by only wrapping the same dense expression across more lines. Simplify the structure.

# Commits

- Make small commits that each do one thing and read on their own: one module, one fix, one UI piece, or one config change. Do not put a whole feature or a whole round of review fixes in one commit.
- Commit as the work progresses. When the working tree holds several changes, split them into separate commits with `git add <paths>` or `git add -p`.
- Order commits so each builds on the one before, and put tests in the same commit as the code they cover.
- Give each commit a subject that says what changed and a short body that says why.

# Codex commit attribution

This PC is also used by the human repository owner. New commits created by
Codex in this repository must use this identity for both author and committer:

- Name: `AndrewVelezMuse`
- Email: `336336425+AndrewVelezMuse@users.noreply.github.com`

Apply the identity to each Git command rather than changing saved Git settings:

```powershell
git -c user.name=AndrewVelezMuse -c user.email=336336425+AndrewVelezMuse@users.noreply.github.com commit -m "Commit message"
```

- Use the same command-scoped overrides for other Git operations that create commits.
- Check for conflicting `GIT_AUTHOR_*` and `GIT_COMMITTER_*` environment variables before committing, and verify the resulting author and committer before pushing.
- Preserve existing authors when amending, cherry-picking, or rebasing unless the user explicitly requests an authorship change. Use the Muse identity for Codex's committer identity.
- Keep the human's saved repository and global Git identity settings intact. Do not rewrite prior commit attribution to apply this preference.
