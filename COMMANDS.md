# Commands
Commands are typed into your profile comment.

1. Open your Profile and edit your comment
2. Type a command (they all start with `/`) and exit the profile page to save
3. Your items update right away. The command isn't kept as your comment

# Items
`<amount>` is 1 to 100,000,000 per command. Nothing goes past 999,999,999 no matter how many times you run a command.

| Command | Gives |
|---|---|
| `/give coins <amount>` | Coins |
| `/give virtual_coins <amount>` | Virtual Live coins |
| `/give materials <amount>` | Every material |
| `/give material <materialId> <amount>` | One material |
| `/give scores <amount>` | Every practice score |
| `/give score <practiceTicketId> <amount>` | One practice score |
| `/give skill_scores <amount>` | Every skill practice score |
| `/give skill_score <skillPracticeTicketId> <amount>` | One skill practice score |
| `/give gacha_tickets <amount>` | Every gacha ticket |
| `/give gacha_ticket <gachaTicketId> <amount>` | One gacha ticket |
| `/give card <cardId>` | A card (one more copy if you have it already) |

Examples
```
/give coins 1000000
/give materials 500
/give material 17 100
/give card 1
```

# Decks
`/d <deck> <card1>,<card2>,<card3>,<card4>,<card5>`

Sets deck 1 to 10 to exactly five cards, the first one as leader. The cards are separated by commas only, NO SPACES! Use card IDs.

```
/d 1 1,2,3,4,5
```

# IDs
IDs come from the game's master data: `materials`, `practiceTickets`, `skillPracticeTickets`, `gachaTickets` and `cards`. You can find them here:
- [Materials](https://github.com/Sekai-World/sekai-master-db-diff/blob/421d413b84892a9419dc0e8b7171f07c08a8f577/materials.json)
- [Practice Tickets](https://github.com/Sekai-World/sekai-master-db-diff/blob/421d413b84892a9419dc0e8b7171f07c08a8f577/practiceTickets.json) (Card Levelup Items)
- [Skill Practice Tickets](https://github.com/Sekai-World/sekai-master-db-diff/blob/421d413b84892a9419dc0e8b7171f07c08a8f577/skillPracticeTickets.json) (Skill Levelup Items)
- [Gacha Tickets](https://github.com/Sekai-World/sekai-master-db-diff/blob/421d413b84892a9419dc0e8b7171f07c08a8f577/gachaTickets.json)
- [Cards](https://github.com/Sekai-World/sekai-master-db-diff/blob/421d413b84892a9419dc0e8b7171f07c08a8f577/cards.json)

### Commonly Used IDs
| Item | Material ID | Command |
|---|---|---|
| Wish Pieces (想いのカケラ) | 15 | `/give material 15 <amount>` |
| Dream Jewels (想いの純結晶) | 16 | `/give material 16 <amount>` |

# Errors
| Message | Meaning |
|---|---|
| `unknown command` | Not one of the commands above (check the spelling) |
| `missing amount` / `missing id or amount` | Something is missing after the command |
| `invalid amount` / `invalid id` | Not a whole number |
| `amount out of allowed range (1-100000000)` | Amount is 0, negative or over 100,000,000 |
| `unknown id` / `unknown cardId` | No item or card has that ID |
| `invalid deck id` | Deck is not 1 to 10 |
| `there must be exactly 5 cards in the deck` | Not five cards, or spaces between them |
| `invalid cards found in deck` / `card not owned` | A card ID doesn't exist, or you don't have it |
