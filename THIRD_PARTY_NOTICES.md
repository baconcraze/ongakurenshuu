# Third-party components

Ongaku Renshuu's own code is under the MIT license (see LICENSE). It includes or uses the following:

| Component | Where | License | Notes |
|---|---|---|---|
| alphaTab 1.8.4 | `vendor/alphaTab.min.js` | MPL-2.0 (`vendor/alphaTab-LICENSE.txt`) | Unmodified build, used to read Guitar Pro files. Source: https://github.com/CoderLine/alphaTab |
| VexFlow 4.2.5 | `vendor/vexflow-bravura.js` | MIT (`vendor/vexflow-LICENSE.txt`) | Unmodified build, used to draw sheet music. Source: https://github.com/vexflow/vexflow |
| Bravura music font | inside `vendor/vexflow-bravura.js` | SIL Open Font License 1.1 | By Steinberg Media Technologies, bundled with VexFlow. https://github.com/steinbergmedia/bravura |
| Sonivox SoundFont | `soundfonts/sonivox.sf2` | Apache-2.0 (`soundfonts/sonivox-LICENSE.txt`) | Copyright (c) 2004-2006 Sonic Network Inc. Small default instrument set. |
| GeneralUser GS | downloaded by `ongaku --get-soundfont` | GeneralUser GS License v2.0 | Not included in this repository. By S. Christian Collins: https://github.com/mrbumpy409/GeneralUser-GS |
| YouTube IFrame Player API | loaded at runtime from youtube.com | YouTube Terms of Service | Only when the Video panel is used. |
| Barlow and Barlow Condensed | loaded at runtime from Google Fonts | SIL Open Font License 1.1 | Falls back to system fonts when offline. |
