"""The reviewed topic pool for the Daily Challenge: 16 categories, 90+ subtopics, each with a scenario and the CEFR band it suits.

Pure data plus one tiny dataclass. Nothing here talks to a database or a provider. Every subtopic is a *scenario seed*: the script writer still writes its own
original script, and the quality gates (app/listening/daily_gates.py) still compare the finished lesson to everything published before.

Levels: only A1-B2. C1 and C2 are deliberately absent until the generation pipeline has been shown to produce and validate them reliably (config cannot enable them).
"""
from dataclasses import dataclass

LEVELS = ('A1', 'A2', 'B1', 'B2')              # the ONLY levels the daily pipeline may produce
_ORDER = {level: n for n, level in enumerate(LEVELS)}


@dataclass(frozen=True)
class Subtopic:
    key: str
    category: str
    topic: str            # becomes Spec.topic (<= 120 chars)
    scenario: str         # becomes Spec.scenario (<= 200 chars): who, where, what is happening
    lo: str = 'A1'
    hi: str = 'B2'

    def suits(self, cefr: str) -> bool:
        return cefr in _ORDER and _ORDER[self.lo] <= _ORDER[cefr] <= _ORDER[self.hi]


# category -> list of (key, topic, scenario, lo, hi)
_RAW: dict[str, list[tuple[str, str, str, str, str]]] = {
    'travel': [
        ('lost-luggage', 'Reporting lost luggage at an airport', 'A traveller describes a missing suitcase to a baggage-claim agent.', 'A2', 'B2'),
        ('hostel-checkin', 'Checking in to a hostel', 'A backpacker arrives late and asks about beds, lockers and breakfast.', 'A1', 'B1'),
        ('train-delay', 'A delayed train announcement', 'A station announcer explains a delay and the new platform.', 'A1', 'B1'),
        ('rental-car', 'Collecting a rental car', 'A customer and an agent go through insurance, fuel and the return time.', 'B1', 'B2'),
        ('ferry-island', 'Taking the ferry to an island', 'Two friends ask a ticket seller about times, prices and the weather.', 'A1', 'A2'),
        ('hotel-noise', 'Complaining politely about hotel noise', 'A guest calls reception about loud music and asks for another room.', 'A2', 'B2'),
    ],
    'food_dining': [
        ('market-spices', 'Buying spices at a street market', 'A shopper asks a stall owner what each spice tastes like.', 'A1', 'B1'),
        ('cooking-class', 'A beginner cooking class', 'A chef explains the steps of a simple soup to new students.', 'A2', 'B2'),
        ('birthday-cake', 'Ordering a birthday cake', 'A parent chooses flavour, size and pickup time at a bakery counter.', 'A1', 'A2'),
        ('food-allergy', 'Explaining a food allergy', 'A diner tells a waiter which ingredients are unsafe and asks for options.', 'A2', 'B2'),
        ('picnic-plan', 'Planning a picnic', 'Two friends decide what to bring and who carries what.', 'A1', 'B1'),
        ('food-truck', 'A food truck lunch rush', 'A cook calls orders while a customer asks about the daily special.', 'A2', 'B1'),
    ],
    'health_wellbeing': [
        ('pharmacy-cold', 'Asking a pharmacist about a cold', 'A customer lists symptoms and the pharmacist suggests what to take.', 'A1', 'B1'),
        ('dentist-visit', 'A dentist appointment', 'A dentist explains a check-up and gives advice on brushing.', 'A2', 'B1'),
        ('sleep-tips', 'A radio talk about better sleep', 'A presenter shares four habits that help people sleep well.', 'B1', 'B2'),
        ('gym-trial', 'Joining a gym with a trial week', 'A coach shows a new member the machines and the class timetable.', 'A2', 'B1'),
        ('doctor-sore-knee', 'Describing a sore knee to a doctor', 'A runner explains when the pain started and what makes it worse.', 'A2', 'B2'),
        ('stress-walk', 'Using a walk to manage stress', 'A friend explains why short walks help her after a busy day.', 'B1', 'B2'),
    ],
    'home_family': [
        ('moving-house', 'Moving to a new flat', 'Two siblings carry boxes and argue kindly about where the sofa goes.', 'A1', 'B1'),
        ('grandma-recipe', 'Learning a recipe from a grandparent', 'A grandmother explains how she makes dumplings, step by step.', 'A2', 'B1'),
        ('chore-chart', 'Sharing household chores', 'A family agrees who cleans, cooks and takes out the rubbish this week.', 'A1', 'A2'),
        ('new-baby', 'Welcoming a new baby cousin', 'An aunt tells a relative about the birth and plans a visit.', 'A2', 'B1'),
        ('landlord-repair', 'Asking a landlord to fix a leak', 'A tenant describes a dripping pipe and agrees a repair day.', 'B1', 'B2'),
        ('pet-adoption', 'Adopting a rescue dog', 'A volunteer asks a family questions before they take a dog home.', 'A2', 'B2'),
    ],
    'shopping_money': [
        ('return-shoes', 'Returning a pair of shoes', 'A customer explains the problem and asks for a refund or exchange.', 'A2', 'B1'),
        ('second-hand-bike', 'Buying a second-hand bike', 'A buyer asks a seller about age, gears and the price.', 'A2', 'B2'),
        ('bank-account', 'Opening a bank account', 'A clerk explains the documents needed and the monthly fee.', 'B1', 'B2'),
        ('sale-season', 'Shopping in the sales', 'Two friends compare discounts and decide what is really worth buying.', 'A1', 'B1'),
        ('budget-month', 'Planning a monthly budget', 'A couple review rent, food and savings and set a spending limit.', 'B1', 'B2'),
        ('online-delivery', 'Tracking an online delivery', 'A customer phones support about a parcel that has not arrived.', 'A2', 'B2'),
    ],
    'education_study': [
        ('library-card', 'Getting a library card', 'A librarian explains borrowing limits and how to renew books.', 'A1', 'A2'),
        ('study-group', 'Organising a study group', 'Three classmates choose days, a place and who explains what.', 'A2', 'B1'),
        ('exam-nerves', 'Talking about exam nerves', 'A student and a tutor discuss a revision plan for next week.', 'B1', 'B2'),
        ('lecture-intro', 'The start of a short lecture', 'A lecturer outlines the topic, the schedule and the reading.', 'B1', 'B2'),
        ('school-trip', 'Announcing a school trip', 'A teacher tells the class what to bring and when the bus leaves.', 'A1', 'A2'),
        ('language-exchange', 'A language exchange meeting', 'Two people swap tips for practising each other’s languages.', 'A2', 'B2'),
    ],
    'technology_media': [
        ('phone-battery', 'Fixing a phone battery problem', 'A repair-shop worker explains why a battery drains quickly.', 'A2', 'B1'),
        ('video-call', 'Setting up a video call for grandparents', 'A grandchild talks a grandparent through the first call.', 'A1', 'B1'),
        ('podcast-review', 'Recommending a podcast', 'A friend explains why a podcast about small inventions is worth hearing.', 'B1', 'B2'),
        ('password-safety', 'Choosing safer passwords', 'An IT helper explains simple rules for strong passwords.', 'B1', 'B2'),
        ('smart-speaker', 'Using a smart speaker', 'A new owner asks it for the weather and gets confused by a command.', 'A1', 'A2'),
        ('photo-backup', 'Backing up holiday photos', 'Two friends decide where to save their photos so nothing is lost.', 'A2', 'B1'),
    ],
    'nature_environment': [
        ('recycling-rules', 'Learning the recycling rules', 'A neighbour explains which bin is for paper, glass and plastic.', 'A1', 'A2'),
        ('birdwatching', 'A morning of birdwatching', 'A guide points out three birds and what makes each one special.', 'A2', 'B2'),
        ('hiking-weather', 'Checking the weather before a hike', 'Hikers read a forecast and decide whether to start early.', 'A2', 'B1'),
        ('community-garden', 'Starting a community garden', 'Volunteers plan beds, tools and a watering rota.', 'B1', 'B2'),
        ('beach-cleanup', 'Joining a beach clean-up', 'An organiser hands out gloves and explains what to collect.', 'A1', 'B1'),
        ('solar-panels', 'Considering solar panels for a house', 'A homeowner asks an adviser about cost and how long they last.', 'B1', 'B2'),
    ],
    'sports_leisure': [
        ('football-match', 'Describing a football match', 'A fan tells a friend about a late goal and a missed chance.', 'A2', 'B1'),
        ('swimming-lesson', 'A first swimming lesson', 'An instructor reassures an adult beginner in the shallow end.', 'A1', 'A2'),
        ('cycling-club', 'Joining a weekend cycling club', 'A member explains the routes, the pace and the safety rules.', 'B1', 'B2'),
        ('yoga-class', 'A morning yoga class', 'A teacher guides a group through breathing and three simple poses.', 'A2', 'B1'),
        ('board-game-night', 'A board game night', 'Friends read the rules and argue about a surprising move.', 'A2', 'B2'),
        ('marathon-training', 'Training for a first marathon', 'A runner shares how she plans long runs and rest days.', 'B1', 'B2'),
    ],
    'culture_arts': [
        ('museum-guide', 'A short museum tour', 'A guide introduces a painting and the story behind it.', 'A2', 'B2'),
        ('street-festival', 'A street festival', 'A visitor asks a volunteer about the music, the food and the parade.', 'A1', 'B1'),
        ('book-club', 'A book club discussion', 'Members share what they liked about the ending of a novel.', 'B1', 'B2'),
        ('pottery-workshop', 'A pottery workshop', 'A potter shows beginners how to centre clay on the wheel.', 'A2', 'B1'),
        ('local-tradition', 'Explaining a local New Year tradition', 'A student tells a classmate how her family celebrates.', 'A1', 'B1'),
        ('cinema-choice', 'Choosing a film at the cinema', 'Two friends compare trailers and agree on a showing time.', 'A1', 'A2'),
    ],
    'community_civic': [
        ('town-meeting', 'A neighbourhood meeting', 'Residents discuss a new pedestrian crossing near the school.', 'B1', 'B2'),
        ('volunteer-signup', 'Signing up as a volunteer', 'A coordinator explains the tasks and the weekly time commitment.', 'A2', 'B1'),
        ('lost-property', 'Reporting lost property', 'A passenger describes a bag left on a bus to a lost-property clerk.', 'A1', 'B1'),
        ('post-office', 'Sending a parcel abroad', 'A customer compares speed and price at a post-office counter.', 'A2', 'B1'),
        ('local-election', 'A short local election notice', 'A recorded message tells residents where and when to vote.', 'B1', 'B2'),
        ('fire-drill', 'A fire drill announcement', 'A building manager explains the exits and the meeting point.', 'A1', 'A2'),
    ],
    'work_careers': [
        ('interview-prep', 'Preparing for a job interview', 'Two friends practise answers about strengths and experience.', 'B1', 'B2'),
        ('first-day', 'A first day at a new job', 'A colleague shows a newcomer the desk, the kitchen and the rules.', 'A2', 'B1'),
        ('schedule-change', 'Changing a meeting time', 'Two colleagues find a slot that works for everyone.', 'A2', 'B1'),
        ('career-change', 'Changing careers', 'A former nurse explains why she is training to become a designer.', 'B1', 'B2'),
        ('remote-work', 'Working from home', 'A manager and an employee agree days in the office and tools to use.', 'B1', 'B2'),
    ],
    'transport_city': [
        ('bus-directions', 'Asking for bus directions', 'A visitor asks a local which bus goes to the old town and where to change.', 'A1', 'A2'),
        ('bike-share', 'Using a bike-share scheme', 'A user asks a helper how to unlock a bike and where to return it.', 'A2', 'B1'),
        ('taxi-airport', 'Taking a taxi to the airport', 'A passenger and a driver chat about traffic and the quickest road.', 'A2', 'B1'),
        ('parking-fine', 'Questioning a parking fine', 'A driver explains the situation and asks how to appeal.', 'B1', 'B2'),
        ('city-tour-audio', 'An audio walking tour', 'A recorded guide leads listeners past a bridge, a square and a market.', 'B1', 'B2'),
        ('metro-map', 'Reading a metro map', 'A tourist and a station worker plan a route with one change.', 'A1', 'A2'),
    ],
    'science_curiosity': [
        ('why-sky-blue', 'Why the sky looks blue', 'A parent explains light and colour to a curious child.', 'A2', 'B1'),
        ('space-station', 'Life on a space station', 'A short radio feature describes a day for astronauts.', 'B1', 'B2'),
        ('weather-science', 'How rain forms', 'A teacher uses a simple demonstration to show the water cycle.', 'A2', 'B1'),
        ('sleep-animals', 'How animals sleep', 'A presenter compares sleeping habits of dolphins, bats and giraffes.', 'B1', 'B2'),
        ('invention-story', 'A small invention that changed daily life', 'A speaker tells the story of the zip fastener.', 'B1', 'B2'),
        ('science-fair', 'A school science fair', 'A student explains her plant-growth experiment to a visitor.', 'A2', 'B1'),
    ],
    'history_stories': [
        ('old-photo', 'The story behind an old photograph', 'A grandfather describes the street and people in a family photo.', 'A2', 'B1'),
        ('town-origin', 'How a town got its name', 'A local historian tells a short, light-hearted legend.', 'B1', 'B2'),
        ('lighthouse-keeper', 'A lighthouse keeper’s night', 'A narrator describes a stormy night and a ship in trouble.', 'B1', 'B2'),
        ('folk-tale', 'A short folk tale', 'A storyteller tells a tale about a clever fox and a farmer.', 'A1', 'A2'),
        ('first-bicycle', 'The first bicycles', 'A museum guide explains how early bicycles looked and felt.', 'B1', 'B2'),
        ('childhood-memory', 'A childhood summer memory', 'A woman remembers a trip to her uncle’s farm.', 'A2', 'B1'),
    ],
    'hobbies_crafts': [
        ('knitting-circle', 'A knitting circle', 'A group trade tips on patterns while one member fixes a mistake.', 'A2', 'B1'),
        ('photo-walk', 'A photography walk', 'A tutor explains light, framing and patience to a small group.', 'B1', 'B2'),
        ('guitar-lesson', 'A first guitar lesson', 'A teacher shows three chords and asks the student to try them.', 'A1', 'A2'),
        ('diy-shelf', 'Building a shelf', 'Two neighbours measure, drill and decide how high to hang it.', 'A2', 'B1'),
        ('garden-seeds', 'Choosing seeds for a balcony garden', 'A shop assistant recommends herbs that grow well in small pots.', 'A1', 'B1'),
        ('chess-club', 'A chess club evening', 'A coach explains an opening and a beginner asks why it matters.', 'B1', 'B2'),
    ],
}

TAXONOMY: tuple[Subtopic, ...] = tuple(Subtopic(key, category, topic, scenario, lo, hi) for category, rows in _RAW.items() for key, topic, scenario, lo, hi in rows)
CATEGORIES: tuple[str, ...] = tuple(_RAW)
BY_KEY: dict[str, Subtopic] = {s.key: s for s in TAXONOMY}

# The library workflow records one of five coarse categories (library.CATEGORIES). The daily taxonomy is finer; this is the many-to-one mapping.
LIBRARY_CATEGORY = {'travel': 'travel', 'transport_city': 'travel', 'food_dining': 'everyday', 'health_wellbeing': 'everyday', 'home_family': 'everyday',
                    'shopping_money': 'everyday', 'community_civic': 'everyday', 'work_careers': 'workplace', 'education_study': 'workplace',
                    'technology_media': 'workplace', 'culture_arts': 'culture', 'sports_leisure': 'culture', 'hobbies_crafts': 'culture',
                    'nature_environment': 'culture', 'science_curiosity': 'stories', 'history_stories': 'stories'}
# Categories whose scenarios read as "an office" or "a café/restaurant". Together they may fill only a small share of recent days (see daily_planner).
OVERUSED = frozenset({'work_careers', 'food_dining'})

assert len(CATEGORIES) >= 12 and len(TAXONOMY) >= 80 and len(BY_KEY) == len(TAXONOMY)
assert all(len(s.topic) <= 120 and len(s.scenario) <= 200 and len(s.key) <= 40 for s in TAXONOMY)
assert set(LIBRARY_CATEGORY) == set(CATEGORIES)
