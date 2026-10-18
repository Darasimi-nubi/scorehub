"""Written guides: one page per major competition, and a few explainers.

Only facts that stay true for years are stated here (how a competition is organised, how a rule works).
Season-specific facts such as who is top or who won last year live on the live pages, not in this text.
"""

# slug -> page. "names" are the competition names as stored in our database.
LEAGUES = {
    "premier-league": {
        "names": ["Premier League"], "title": "Premier League guide", "country": "England",
        "intro": "The Premier League is the top division of English football and one of the most watched sports leagues in the world.",
        "body": [
            "Twenty clubs play each other twice, once at home and once away, which makes 38 matches per club. A win earns three points, a draw one and a defeat none. The season runs from August to May.",
            "When clubs finish level on points, the table is separated by goal difference (goals scored minus goals conceded), then by goals scored. The champions are simply the club with the most points after all 38 matches; there is no play-off.",
            "The bottom three clubs are relegated to the Championship, the second tier, and three clubs are promoted in their place. The clubs at the top of the table qualify for European competition the following season, and the number of places depends on how English clubs performed in Europe.",
            "English clubs also play in two domestic cup competitions, the FA Cup and the League Cup, so the Premier League is only one part of a big club's season. You can follow every fixture, live score and the current table on ScoreHub.",
        ],
    },
    "la-liga": {
        "names": ["Primera Division", "La Liga"], "title": "La Liga guide", "country": "Spain",
        "intro": "La Liga, called Primera División in the data we use, is the top division of Spanish football.",
        "body": [
            "Twenty clubs play 38 matches each, home and away. Three points for a win, one for a draw. The season runs from August to May.",
            "La Liga separates clubs level on points differently from England: the result of the matches between the tied clubs comes first, and overall goal difference comes later. That is why a club can finish above another with a worse goal difference.",
            "The bottom three clubs are relegated to the Segunda División. The top clubs qualify for UEFA competitions, and Spain's other major trophies, the Copa del Rey and the Supercopa, are played alongside the league.",
            "La Liga is home to some of the best-known clubs in the world. Follow the live scores and the current table on ScoreHub.",
        ],
    },
    "serie-a": {
        "names": ["Serie A"], "title": "Serie A guide", "country": "Italy",
        "intro": "Serie A is the top division of Italian football, known for tactical, defence-minded football and a long history.",
        "body": [
            "Twenty clubs play 38 matches each, home and away. A win is worth three points, a draw one. The season runs from August to May.",
            "The club with the most points wins the Scudetto, the small shield badge worn on the shirts of the champions the following season. The bottom three clubs are relegated to Serie B.",
            "Italian clubs also compete in the Coppa Italia, and the top finishers in Serie A qualify for European competition. Check the live scores and the current table on ScoreHub.",
        ],
    },
    "bundesliga": {
        "names": ["Bundesliga"], "title": "Bundesliga guide", "country": "Germany",
        "intro": "The Bundesliga is Germany's top division and has some of the best-attended stadiums in football.",
        "body": [
            "It has 18 clubs rather than 20, so each club plays 34 matches, home and away. Three points for a win, one for a draw. The season runs from August to May, with a winter break in the middle.",
            "Clubs level on points are separated by goal difference, then goals scored. The bottom two clubs go down to the 2. Bundesliga, and the club in 16th place plays a two-leg play-off against the third-placed club from the second division for a place in the next season.",
            "The Bundesliga is known for its fan ownership rule, which keeps most clubs majority-owned by their members, and for cheap tickets and standing areas. Follow the live scores and table on ScoreHub.",
        ],
    },
    "ligue-1": {
        "names": ["Ligue 1"], "title": "Ligue 1 guide", "country": "France",
        "intro": "Ligue 1 is the top division of French football.",
        "body": [
            "The league has 18 clubs, so each plays 34 matches, home and away. A win earns three points and a draw one. The season runs from August to May.",
            "The club with the most points is champion. The bottom clubs are relegated to Ligue 2, and clubs from Ligue 2 are promoted in their place. The top clubs qualify for European competition.",
            "France's other major competitions include the Coupe de France. Follow every Ligue 1 match, live score and the current table on ScoreHub.",
        ],
    },
    "championship": {
        "names": ["Championship"], "title": "EFL Championship guide", "country": "England",
        "intro": "The Championship is the second tier of English football, one level below the Premier League.",
        "body": [
            "It has 24 clubs, and each plays 46 matches, home and away, which makes it one of the longest league seasons in Europe. A win earns three points and a draw one. The season runs from August to May.",
            "The top two clubs are promoted automatically to the Premier League. The clubs in third to sixth place play a two-leg semi-final and then a one-match final at Wembley for the last promotion place.",
            "The bottom three clubs are relegated to League One. Because the stakes at both ends of the table are so high, the Championship is known for being unpredictable. Follow every match and the table on ScoreHub.",
        ],
    },
    "eredivisie": {
        "names": ["Eredivisie"], "title": "Eredivisie guide", "country": "Netherlands",
        "intro": "The Eredivisie is the top division of Dutch football and is known for developing young players.",
        "body": [
            "Eighteen clubs play 34 matches each, home and away. A win is worth three points and a draw one. The season runs from August to May.",
            "Clubs such as Ajax, PSV and Feyenoord have long been known for their academies, and many of Europe's best players passed through the Eredivisie early in their careers. Follow the live scores and the table on ScoreHub.",
        ],
    },
    "primeira-liga": {
        "names": ["Primeira Liga"], "title": "Primeira Liga guide", "country": "Portugal",
        "intro": "The Primeira Liga is the top division of Portuguese football.",
        "body": [
            "It has 18 clubs, and each plays 34 matches, home and away. A win earns three points and a draw one. The season runs from August to May.",
            "Portuguese football has long been dominated by Benfica, Porto and Sporting CP, often called the three big clubs. The league is a well-known place for talent to be scouted before moving to bigger leagues. Follow the live scores and table on ScoreHub.",
        ],
    },
    "brasileirao": {
        "names": ["Campeonato Brasileiro Série A"], "title": "Brasileirão Série A guide", "country": "Brazil",
        "intro": "The Campeonato Brasileiro Série A, often called the Brasileirão, is the top division of Brazilian football.",
        "body": [
            "Twenty clubs play 38 matches each, home and away. A win is worth three points and a draw one. Unlike most European leagues, the season follows the calendar year, from around April to December.",
            "The bottom clubs are relegated to Série B. Brazilian clubs also play in the Copa do Brasil and, for the best finishers, the Copa Libertadores, the main club competition in South America. Follow every match on ScoreHub.",
        ],
    },
    "champions-league": {
        "names": ["UEFA Champions League", "Champions League"], "title": "UEFA Champions League guide", "country": "Europe",
        "intro": "The UEFA Champions League is the top club competition in Europe, played between the best clubs from the continent's leagues.",
        "body": [
            "Since the 2024–25 season the competition begins with a single league phase of 36 clubs. Each club plays eight matches against eight different opponents, four at home and four away, and all clubs are placed in one table.",
            "The top eight clubs go straight to the round of 16. The clubs in ninth to 24th place play a two-leg knockout play-off for the other eight places, and the clubs below that are eliminated. From the round of 16 onwards, ties are played over two legs, home and away, except for the final, which is a single match at a neutral stadium.",
            "The competition is organised by UEFA. Clubs qualify through their league position the season before, and the winners of the Champions League and the Europa League also qualify for the next one. Follow every match and the league table on ScoreHub.",
        ],
    },
    "europa-league": {
        "names": ["Europa League", "UEFA Europa League"], "title": "UEFA Europa League guide", "country": "Europe",
        "intro": "The UEFA Europa League is Europe's second-biggest club competition, one step below the Champions League.",
        "body": [
            "It uses the same format as the Champions League: a league phase of 36 clubs, each playing eight matches, followed by knockout rounds. The top clubs advance directly and the middle group play a knockout play-off.",
            "The winner of the Europa League earns a place in the following season's Champions League, which makes the competition important for clubs that did not qualify through their domestic league. Follow the live scores on ScoreHub.",
        ],
    },
    "npfl": {
        "names": ["NPFL", "Nigeria Premier Football League"], "title": "NPFL guide", "country": "Nigeria",
        "intro": "The Nigeria Premier Football League, or NPFL, is the top division of football in Nigeria.",
        "body": [
            "The league is run by the League Management Company (LMC) and is played by 20 clubs from across the country, including well-known names such as Enyimba of Aba, Kano Pillars, Rivers United, Remo Stars, Plateau United and Shooting Stars. Clubs play each other home and away over the season, and a win is worth three points.",
            "The top clubs qualify for the CAF Champions League and the CAF Confederation Cup, Africa's two main club competitions, and the bottom clubs are relegated to the second division, the Nigeria National League.",
            "Home matches are a major part of local football culture, and club support is strongly tied to cities and states. ScoreHub shows NPFL fixtures and results at the top of the football list so that Nigerian supporters can find them first.",
        ],
    },
    "afcon": {
        "names": ["Africa Cup of Nations", "AFCON Qualifiers"], "title": "Africa Cup of Nations (AFCON) guide", "country": "Africa",
        "intro": "The Africa Cup of Nations, known as AFCON, is the main national-team tournament in Africa.",
        "body": [
            "The tournament is organised by the Confederation of African Football (CAF). Since 2019 it has been played by 24 national teams, who are drawn into groups. The best teams from each group and the best third-placed teams advance to the knockout rounds, and the final decides the champion.",
            "National teams earn their place through a qualification competition played over the preceding months, which is also shown on ScoreHub under AFCON Qualifiers.",
            "Nigeria's national team, the Super Eagles, has won the tournament three times, in 1980, 1994 and 2013. Follow all Nigeria matches and every AFCON fixture on ScoreHub.",
        ],
    },
    "caf-champions-league": {
        "names": ["CAF Champions League"], "title": "CAF Champions League guide", "country": "Africa",
        "intro": "The CAF Champions League is the top club competition in Africa, played between the best clubs from CAF's member countries.",
        "body": [
            "Clubs qualify through their league position in their own country, such as the NPFL in Nigeria. After early knockout rounds, the remaining clubs are drawn into groups, and the best clubs from each group advance to the knockout stages. Ties are played over two legs, home and away.",
            "The competition is organised by the Confederation of African Football (CAF), and the winners represent Africa at the FIFA Club World Cup. Follow every CAF Champions League match on ScoreHub.",
        ],
    },
    "caf-confederation-cup": {
        "names": ["CAF Confederation Cup"], "title": "CAF Confederation Cup guide", "country": "Africa",
        "intro": "The CAF Confederation Cup is Africa's second club competition, one step below the CAF Champions League.",
        "body": [
            "It is open to clubs that qualify through their domestic league or cup, and follows a similar structure of early knockout rounds, then a group stage, then knockout rounds played over two legs. The final decides the champion.",
            "It is a good chance for clubs that did not qualify for the Champions League to win a continental trophy. Follow every match on ScoreHub.",
        ],
    },
}

# Explainers: slug -> {title, desc, body (paragraphs; a string starting with "## " is a heading)}
ARTICLES = {
    "how-a-football-league-table-works": {
        "title": "How a football league table works",
        "desc": "Points, goal difference and tie-breakers explained.",
        "body": [
            "A league table ranks every club by how well it has done so far. It looks simple, but the details are what decide titles, European places and relegation.",
            "## Points",
            "In almost every league a win earns three points, a draw earns one and a defeat earns none. Clubs are ranked by total points. Before 1981 in England, a win earned only two points, which is why older tables look different.",
            "## When clubs have the same points",
            "Leagues use tie-breakers. In England, Germany and many other leagues, the first tie-breaker is goal difference, which is goals scored minus goals conceded. If that is also level, the next tie-breaker is goals scored. In Spain, the results between the tied clubs come first, which can put a club with a worse overall goal difference ahead.",
            "## The columns you will see",
            "P is matches played. W, D and L are wins, draws and losses. GF and GA are goals for and goals against. GD is goal difference, and Pts is points. Some tables also show the result of each club's last five matches, with W, D and L.",
            "## What the positions mean",
            "The club at the top is the leader, and the title goes to whoever is top after the last match. Places near the top usually bring entry to European competition. The bottom places are relegation positions: those clubs drop to the next division. Each league sets its own numbers, which is why the same position can mean different things in different countries.",
            "## Why matches played matters",
            "A club can be higher in the table with fewer points if it has played fewer matches, and a club with a game in hand has more chance to catch up. Always check the P column when comparing clubs.",
        ],
    },
    "the-offside-rule-explained": {
        "title": "The offside rule explained simply",
        "desc": "When a player is offside, and when they are not.",
        "body": [
            "Offside is the rule that confuses new supporters most, but the basic idea is simple: an attacker should not wait near the opponent's goal for a pass.",
            "## The basic rule",
            "A player is in an offside position if, at the moment a teammate plays the ball to them, any part of their head, body or legs that can score a goal is closer to the opponent's goal line than both the ball and the second-last defender. The goalkeeper usually counts as one of the defenders, so in practice the question is whether the attacker is ahead of the last outfield defender.",
            "## Being in an offside position is not an offence",
            "A player is only penalised if they take part in the play: playing the ball, interfering with an opponent or gaining an advantage from the position. Standing offside while the ball goes elsewhere is fine.",
            "## When a player cannot be offside",
            "A player cannot be offside from a goal kick, a throw-in or a corner kick. A player also cannot be offside in their own half of the pitch, or when level with the second-last defender or with the last two defenders.",
            "## Offside and VAR",
            "Video assistant referees check goals for offside using lines drawn on the replay. This is why a goal can be cancelled a minute after it was scored, and why live scores in apps sometimes drop by one. ScoreHub updates the score when the data provider does, so a goal that is ruled out will disappear shortly afterwards.",
        ],
    },
    "how-to-read-a-match-page": {
        "title": "How to read a ScoreHub match page",
        "desc": "What the summary, stats, line-ups and ratings mean.",
        "body": [
            "Every football match on ScoreHub has its own page. Here is what each part shows and how to use it.",
            "## The score and the minute",
            "At the top you see the score and the match status. A red dot means the match is live and shows the minute, such as 63'. HT means half time, and FT means full time. Scores refresh every few seconds while the page is open.",
            "## Summary",
            "The summary tab lists goals, cards and substitutions in order, with the minute each one happened. A yellow card is a warning and a red card means the player is sent off. VAR entries show when a decision was reviewed.",
            "## Stats",
            "The stats tab compares the two teams. Possession is the share of time each team had the ball. Shots on target are the shots that would have gone in without a save or block. Corners, fouls and offsides show how the match was played. A team can have more of the ball and still lose, so look at shots on target for a better idea of who created the best chances.",
            "## Line-ups",
            "The line-ups tab shows each team's starting eleven laid out as a formation, plus the substitutes. A formation such as 4-3-3 lists the defenders first, then midfielders, then forwards. Player ratings appear after the match and are out of ten.",
            "## Head to head",
            "Head to head lists previous meetings between the two clubs. It does not predict the next result, but it shows how closely matched the clubs have been.",
            "## Follow, star and share",
            "Use Follow to add a team to your list, the star to mark a single match, and Share to send the match to friends. If you turn on alerts, you will be notified of goals, kick-off and full time.",
        ],
    },
    "why-live-scores-differ-between-apps": {
        "title": "Why live scores can differ between apps",
        "desc": "Delays, VAR and data providers explained.",
        "body": [
            "If you compare two live-score apps during a match, you may see different scores or minutes. This is normal, and there are a few clear reasons.",
            "## Different data providers",
            "Most apps do not collect scores themselves. They buy or receive them from data providers, which use people at stadiums or watching the broadcast to enter events. Each provider has its own speed and its own rules for what counts as an update.",
            "## Delays",
            "Free data feeds are often slower than paid ones by a minute or more. TV broadcasts and streams add their own delay, so a notification on your phone can arrive before or after the goal reaches you on screen.",
            "## VAR and corrections",
            "When a goal is checked by VAR, providers often show the goal immediately and remove it if it is ruled out. Own goals and goals that are credited to a different player are also corrected after the match. Final results and scorers can change slightly in the hours after the final whistle.",
            "## The match minute",
            "The minute shown is usually an estimate from kick-off time, so it can drift from the stadium clock. Stoppage time is shown as 45+ or 90+ because the exact added time is only announced by the referee.",
            "## What to do",
            "If a score looks wrong, refresh the page and wait a minute. For official results, the competition's own website is the final source.",
        ],
    },
    "football-formations-explained": {
        "title": "Football formations explained: 4-4-2, 4-3-3 and more",
        "desc": "What the numbers in a line-up mean.",
        "body": [
            "When you see a line-up written as 4-3-3 or 4-4-2, the numbers describe how a team spreads its ten outfield players across the pitch. The goalkeeper is not counted.",
            "## Reading the numbers",
            "The first number is the defenders, the last is the forwards, and the numbers in between are the midfielders. So 4-4-2 is four defenders, four midfielders and two forwards, and 4-3-3 is four defenders, three midfielders and three forwards.",
            "## Common formations",
            "4-4-2 is a classic shape with two banks of four and two strikers. It is balanced and simple. 4-3-3 uses a trio in midfield and three attackers, often a centre-forward with a winger on each side. It is common for teams that want to keep the ball and press high.",
            "3-5-2 and 3-4-3 use three central defenders with wing-backs, who run along the sideline and defend and attack. These shapes can be strong in the middle but depend on the wing-backs' energy.",
            "## Formations change during a match",
            "A formation is a starting point. Teams often defend in one shape and attack in another, and a coach may change the shape with substitutions. A 4-3-3 can become a 4-5-1 without the ball.",
            "## Where to see them",
            "On ScoreHub's line-ups tab, you can see each team's shape laid out on the pitch, with the players' numbers and, after the match, their ratings.",
        ],
    },
    "super-eagles-and-afcon": {
        "title": "The Super Eagles and the Africa Cup of Nations",
        "desc": "Nigeria's history at AFCON and how to follow the team.",
        "body": [
            "Nigeria's national team, the Super Eagles, is one of the best-supported teams in Africa. This guide covers the basics for new fans.",
            "## What AFCON is",
            "The Africa Cup of Nations (AFCON) is the main tournament for national teams in Africa, organised by the Confederation of African Football (CAF). Since 2019 it has been played by 24 teams, who are drawn into groups, followed by knockout rounds and a final.",
            "## Nigeria's record",
            "Nigeria has won the tournament three times: in 1980, 1994 and 2013. The team has also reached many other finals and semi-finals, and has qualified for several FIFA World Cups.",
            "## How a team qualifies",
            "National teams earn a place by playing a qualification competition in groups before the tournament. The best-placed teams in each group qualify, and the host country qualifies automatically. The qualifiers are played over several international windows, so a match can be several months before the tournament itself.",
            "## Other matches to follow",
            "Besides AFCON, the Super Eagles play World Cup qualifiers and friendlies. ScoreHub shows all of these above the European leagues on the football page, so you can find Nigeria matches quickly. Follow Nigeria on the Following page to get an alert for kick-off, goals and full time.",
        ],
    },
}