# Third-party Battlerite assets

The map artwork in `maps/` and the champion portraits in
`champion-emojis/` depict content from *Battlerite*. Battlerite names,
characters, maps, artwork, logos, and related trademarks are the property of
Stunlock Studios AB and their respective rights holders.

These assets are included only to identify Battlerite maps and champions in an
unofficial community integration. This project is not
affiliated with, sponsored by, approved by, or endorsed by Stunlock Studios.
The project and its contributors claim no ownership of the underlying
Battlerite material. Inclusion of an asset is not a statement about its
copyright status, a grant of rights, a legal conclusion that a particular use
is fair use or another exception, or a guarantee that reuse is permitted.

A separately downloaded general Stunlock Studios brand kit contains studio
branding, logos, and photographs, but no Battlerite game artwork. The official
[Battlerite press kit](https://press.stunlock.com/kit/) is a different source
and does include a partial Media collection. That collection includes
high-resolution Orman Temple and Blackstone Arena environment screenshots and
character artwork for champions including Freya, Raigon, Sirius, Destiny,
Ezmo, and Rook.

Those official files are not equivalent replacements for the supplied asset
sets used here. The reviewed Blackstone and Orman press-kit images are
1919×1199 and 1911×1196 environment screenshots, respectively. The supplied
maps are 400×245 labeled, top-down thumbnails and form a complete set of eight
consistently framed day/night pairs. Reviewed press-kit character examples
range from 889×1500 portrait art to roughly 5000 pixels wide, but the
collection is partial and differently framed; the supplied set contains
uniformly framed UI portraits for all 28 champions. The current supplied sets
were therefore retained for consistency and completeness. Availability in a
press kit is not treated as a license or as permission to redistribute any
file. For official information, visit [Stunlock Studios](https://www.stunlock.com/)
and the [Stunlock blog](https://blog.stunlock.com/).

Asset preparation for this repository:

- The 16 map PNGs were supplied at 400×245 and copied without resizing under
  normalized lowercase filenames. The manifest records eight named maps with
  separate day and night variants.
- The 28 supplied champion portraits were 115×69. Each upload helper was
  center-cropped to 69×69, resized to 128×128 with Lanczos filtering, and saved
  under a lowercase, hyphenated canonical champion name. The derived files are
  intended as optional Discord application-emoji upload sources.

Rights holders may request correction or removal through the repository's
[issue tracker](https://github.com/voxix-dev/battlevive-bot/issues).
