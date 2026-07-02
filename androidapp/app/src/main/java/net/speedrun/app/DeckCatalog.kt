// Copyright: Ankitects Pty Ltd and contributors
// License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

package net.speedrun.app

/**
 * A forum-recommended deck the user can download and import with one tap. The
 * URL may be a Google Drive share link; the Library resolves it to a direct
 * download. Add more decks by appending to [POPULAR_DECKS] - one line each.
 */
data class CatalogDeck(
    val name: String,
    val section: String,
    val sizeLabel: String,
    val url: String,
)

/**
 * Curated starter catalog. MileDown is the community "general content" staple on
 * r/MCAT and is verified to import with images. (Pankow P/S and JackSparrow are
 * now folded into the subscription-only AnKing MCAT deck, so they aren't
 * one-tap-installable as static files; paste any direct .apkg link below to add
 * a deck that isn't listed here.)
 */
val POPULAR_DECKS: List<CatalogDeck> = listOf(
    CatalogDeck(
        name = "MileDown",
        section = "Bio/Biochem, Chem/Phys & general content",
        sizeLabel = "~238 MB · includes images",
        url = MILEDOWN_DECK_URL,
    ),
)
