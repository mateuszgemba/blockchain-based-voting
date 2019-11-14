import random
import time
import utils
import json
from constants import *
from datetime import datetime, timedelta
from base import (VotingComputer, VoterAuthenticationBooth, 
    UnrecognizedVoterAuthenticationBooth, AdversaryVotingComputer,
    AuthBypassVoterAuthenticationBooth, UnknownVoter)
from copy import copy, deepcopy
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from exceptions import NotEnoughBallotClaimTickets
from election import Voter, Ballot


def create_nodes(NodeClass, *additional_args, num_nodes=0):
    nodes = []
    for i in range(num_nodes):
        public_key, private_key = utils.get_key_pair()
        args_list = list(additional_args) + [public_key, private_key]
        args = tuple(args_list)
        node = NodeClass(*args)
        nodes.append(node)
    return nodes


def get_pki(nodes):
    pki = dict()
    for node in nodes:
        pki[hash(node.public_key)] = node
    return pki


class VotingProgram:
    path = 'voter_roll.txt'
    num_voters_voted = 0

    def setup(self, 
              adversarial_mode=False, 
              consensus_round_interval=DEFAULT_CONSENSUS_ROUND_INTERVAL,
              total_nodes=50):
        self.adversarial_mode = adversarial_mode
        self.consensus_round_interval = consensus_round_interval
        self.total_nodes = total_nodes
        self.total_adversarial_nodes = 0
        voter_node_adversary_class = None
        voting_node_adversary_class = None
        if self.adversarial_mode:
            self.total_adversarial_nodes = int((1-MINIMUM_AGREEMENT_PCT) * self.total_nodes) - 1
            # TODO: set class, randomize?
            voter_node_adversary_class = AuthBypassVoterAuthenticationBooth#UnrecognizedVoterAuthenticationBooth
            voting_node_adversary_class = AdversaryVotingComputer


        # set up election with ballot template
        self.ballot = Ballot(election='U.S. 2020 Federal Election')
        self.ballot.add_item(
            position='President', 
            description='Head of executive branch', 
            choices=['Obama(D)', 'Bloomberg(R)'], 
            max_choices=1
        )
        self.ballot.add_item(
            position='Vice President',
            description='Executive right below President',
            choices=['Joe Biden(D)', 'Bradley Tusk(R)'],
            max_choices=1
        )
        self.ballot.finalize()

        # load voter roll from file/configuration
        self.load_voter_roll()

        # initialize regular nodes
        num_nodes = self.total_nodes - self.total_adversarial_nodes
        self.voting_computers = create_nodes(
            VotingComputer, self.ballot, num_nodes=num_nodes
        )
        self.voter_authentication_booths = create_nodes(
            VoterAuthenticationBooth, self.voter_roll, num_nodes=num_nodes
        )

        # initialize adversary nodes
        self.voting_computers += (
            create_nodes(voting_node_adversary_class, self.ballot, num_nodes=self.total_adversarial_nodes)
        )
        self.voter_authentication_booths += (
            create_nodes(voter_node_adversary_class, self.voter_roll, num_nodes=self.total_adversarial_nodes)
        )

        # construct copy of PKI and add to all nodes
        voting_nodes_pki = get_pki(self.voting_computers)
        for node in self.voting_computers:
            node.set_node_mapping(copy(voting_nodes_pki))

        voter_auth_nodes_pki = get_pki(self.voter_authentication_booths)
        for node in self.voter_authentication_booths:
            node.set_node_mapping(copy(voter_auth_nodes_pki))

        # initialize blockchain with appropriate content?

    def begin_program(self):
        self.last_time = datetime.now()
        continue_program = True
    
        while continue_program:
            utils.clear_screen()
            self.display_header()
            self.display_menu()
            choice = self.get_menu_choice()
            continue_program = self.handle_menu_choice(choice)
            if self.is_election_over():
                break
            if self.is_consensus_round():
                self.demonstrate_consensus(self.voter_authentication_booths, 'Voter Blockchain')
                self.demonstrate_consensus(self.voting_computers, 'Ballot Blockchain')
            input("Press any key to continue")

        self.demonstrate_consensus(self.voter_authentication_booths, 'Voter Blockchain')
        self.demonstrate_consensus(self.voting_computers, 'Ballot Blockchain')        
        print("Election over! Results: ")
        self.display_results(nodes_in_sync=True)

    def get_menu_choice(self):
        return utils.get_input_of_type('Enter in an option: ', int)

    def is_consensus_round(self):
        if datetime.now() - self.last_time >= timedelta(seconds=self.consensus_round_interval):
            self.last_time = datetime.now()
            return True
        return False

    def demonstrate_consensus(self, nodes, blockchain_name):
        print()
        print('Kicking off consensus round for {}'.format(blockchain_name))
        # step 1 -- achieve consensus on last block hash (aggregate consensus stats)
        hash_agreement = {}
        for node in nodes:
            h = node.blockchain.current_block.hash  # hash contains previous block header, which is signed by particular
            if h in hash_agreement:
                hash_agreement[h].append(node)
            else:
                hash_agreement[h] = [node]
        num_hashes = len(hash_agreement.keys())
        majority_hash = None
        majority_nodes_len = 0

        for h in hash_agreement:
            nodes = hash_agreement[h]
            num_nodes = len(nodes)

            if not majority_hash:
                majority_nodes_len = num_nodes
                majority_hash = h

            elif num_nodes > majority_nodes_len:
                majority_nodes_len = num_nodes
                majority_hash = h

        if majority_hash:
            nodes = hash_agreement[majority_hash]
            for node in nodes:
                node.begin_consensus_round(nodes=nodes.copy())

            for node in nodes:
                node.finalize_consensus_round()

            # compile stats for each node per group
            for node in nodes:
                if not node.is_adversary:
                    good_node = node
                    break
            node = good_node
            num_nodes = len(nodes)

            print('Consensus among {} nodes'.format(num_nodes))
            print('Transactions approved: {}'.format(len(node.last_round_approvals)))
            rejection_msg = 'Transactions rejected: {}'.format(len(node.rejection_map))#last_round_rejections))
            if len(node.last_round_rejections) > 0:
                rejected_reasons = list(set(node.last_round_rejection_reasons))
                rejection_msg = '{} Reason(s): {}'.format(rejection_msg, rejected_reasons)
            print(rejection_msg)
        time.sleep(2)

       
        # step 2 -- run a consensus round among nodes that have the same hash
        """
        for h in hash_agreement:
            #print("Consensus among blocks with hash {}".format(h))
            nodes = hash_agreement[h]
            for node in nodes:
                if num_hashes == 1:
                    node.begin_consensus_round()
                else:
                    # perform consensus only with nodes in agreement of hash
                    node.begin_consensus_round(nodes=nodes.copy())
            for node in nodes:
                node.finalize_consensus_round()

            # compile stats for each node per group
            node = nodes[-1]
            num_nodes = len(nodes)

            print('Consensus among {} nodes'.format(num_nodes))
            print('Transactions approved: {}'.format(len(node.last_round_approvals)))
            rejection_msg = 'Transactions rejected: {}'.format(len(node.last_round_rejections))
            if len(node.last_round_rejections) > 0:
                rejection_msg = '{} Reason(s): {}'.format(rejection_msg, node.last_round_rejection_reasons)
            print(rejection_msg)

        
        time.sleep(2)
        """

    def display_header(self):
        if self.adversarial_mode:
            print("ADVERSARIAL mode")
        print ("{}".format(self.ballot.election))
        print ("Voter Blockchain  | Normal Nodes: {}\t Adversary Nodes: {}".format(
                len(self.voter_authentication_booths) - self.total_adversarial_nodes, 
                self.total_adversarial_nodes
            )
        )
        print ("Ballot Blockchain | Normal Nodes: {}\t Adversary Nodes: {}".format(
                len(self.voting_computers) - self.total_adversarial_nodes, 
                self.total_adversarial_nodes
            )
        )
        next_consensus_round = self.last_time + timedelta(seconds=self.consensus_round_interval)
        print ("Next consensus round: {}".format(
                next_consensus_round.time().strftime("%H:%M:%S")
            )
        )
        print()

    def display_menu(self):
        print ("(1) Vote")
        print ("(2) Lookup voter id")
        print ("(3) View current results")
        print ("(4) View logs")
        print ("(5) Exit")

    def display_results(self, nodes_in_sync=False):
        # get results from all nodes in ballot blockchain
        # TODO: check blockchain results first
        #self.blockchain.current
        # extract ballot from transactions that have consensus from network
        print('Displaying results from the blockchain: ')
        #if nodes_in_sync:
        hash_frequency = {}
        num_nodes = len(self.voting_computers)
        hash_to_block = {}
        # find block that meets minimum consensus requirements
        for node in self.voting_computers:
            block = node.blockchain.current_block
            if block.hash not in hash_frequency:
                hash_frequency[block.hash] = 1
                hash_to_block[block.hash] = block
            else:
                hash_frequency[block.hash] += 1

            if hash_frequency[block.hash]/num_nodes >= MINIMUM_AGREEMENT_PCT:
                print(json.dumps(hash_to_block[block.hash].state, indent=4))
                return

        print('Blocks are not in sync. please wait until next consensus round.')
        return

        '''
        # include majority blockchain state + all node's local transactions that would achieve consensus
        transaction_tally = {}
        for node in self.voting_computers:
            # AGGREGATE all open and verified transactions for all nodes
            for tx in node.verified_transactions:
                if tx in transaction_tally:
                    transaction_tally[tx] += 1
                else:
                    transaction_tally[tx] = 1

        approved_transactions = []
        ballots = []
        network_size = len(self.voting_computers)
        for tx, num_approvals in transaction_tally.items():
            if num_approvals/network_size >= MINIMUM_AGREEMENT_PCT:
                approved_transactions.append(tx)
                ballots.append(tx.content)
        results = Ballot.tally(ballots)

        
        # add blockchain results to it
        blockchain_state = self.voting_computers[0].blockchain.current_block.state
        if not results:
            print(json.dumps(blockchain_state, indent=4))
            return
        else:
            print('Blockchain results:')
            print(json.dumps(results, indent=4))
            print('Pending votes:')
            print(json.dumps(blockchain_state, indent=4))
            return
        for item in blockchain_state:
            #import ipdb; ipdb.set_trace()
            for candidate in blockchain_state[item]:
                results[item][candidate] =+ blockchain_state[item][candidate]
        print(json.dumps(results, indent=4))
        '''

    def handle_menu_choice(self, choice):
        """
        Redirects menu choice to appropriate function.
        Returns whether or not program should continue.
        """
        if choice == 1:
            self.vote()
        elif choice == 2:
            self.lookup_voter_id()
        elif choice == 3:
            self.display_results(nodes_in_sync=False)
        elif choice == 4:
            self.display_logs()
        elif choice == 5:
            return False
        else:
            print("Unrecognized option")
        return True

    def display_logs(self):
        print('Displaying max 30 lines')
        log_file = 'logs/node.log'
        lines = []
        with open(log_file, 'r') as fh:
            for line in fh:
                lines.append(fh.readline().strip())
        for line in lines[-30:]:
            print(line)

    def _authenticate_voter(self, voter_auth_booth):
        """Authenticates voter and returns voter id (None if voter cannot vote)."""
        voter = utils.get_input_of_type(
            "Please authenticate yourself by typing in your full name.\n",
            str
        ).lower()
        voter_id = None

        voters = self.get_voter_by_name(voter)
        if len(voters) > 1:
            voter_id = utils.get_input_of_type(
                "Multiple matches found for {}. Please enter in your voter id.\n".format(voter),
                str
            )
            if voter_id not in [v.id for v in voters]:
                print("Please look up your ID and try again.")
                return None
        elif len(voters) == 1:
            voter_id = voters[0].id

        authenticated = voter_auth_booth.authenticate_voter(voter_id)

        if not authenticated:
            print("{} is not on the voter roll".format(voter))
            return None
        return voter_id

    def vote(self, **kwargs):
        """Simulates voter's experience at authentication and voter booths."""
        
        #voter_auth_booth = random.choice(
        #    self.voter_authentication_booths[-1:] + self.voter_authentication_booths[:1]
        #)
        voter_auth_booth = random.choice(self.voter_authentication_booths)
        voter_id = self._authenticate_voter(voter_auth_booth)
        #if not voter_id:
        #    return

        # try to retrieve ballot claim ticket
        try:
            ballot_claim_ticket = voter_auth_booth.generate_ballot_claim_ticket(voter_id)
            print("Retrieved ballot claim ticket. Please proceed to the voting booths.\n")
        except (NotEnoughBallotClaimTickets, UnknownVoter) as e:
            voter_auth_booth.log(e)
            print(e)
            return

        # vote
        voting_computer = random.choice(self.voting_computers)
        voting_computer.vote(ballot_claim_ticket, **kwargs)

        # TODO: local global counter
        self.num_voters_voted+=1

    def is_election_over(self):
        # check for consensus among global counters from all nodes
        if self.num_voters_voted >= len(self.voter_roll):
            return True
        return False

    def lookup_voter_id(self):
        name = utils.get_input_of_type(
                "Type in your full name: ",
                str
            ).lower()
        matches = self.get_voter_by_name(name)

        if not matches:
            print("No matches found")
        else:
            print("Matching ID(s) found: {}".format(
                [voter.id for voter in matches]
            ))

    def get_voter_by_name(self, name):
        return [voter for voter in self.voter_roll if voter.name == name]

    def load_voter_roll(self):
        voter_roll = []
        voter_id = 1

        with open(self.path, 'r') as file:
            voter_roll_dict = json.load(file)
            for voter in voter_roll_dict:
                name = voter['name'].strip().lower()  # use lowercase for simplicity
                if name:
                    num_claim_tickets = int(voter.get('num_claim_tickets', 1))
                    voter_roll.append(Voter(voter_id, name, num_claim_tickets))
                    voter_id += 1
        print ("Registered voters from {}: {}".format(
            self.path, voter_roll)
        )
        self.voter_roll = voter_roll


class Simulation(VotingProgram):
    """Wrapper for voting program that overrides parts of it for simulation purposes"""
    current_voter_index = 0 
    candidate_one_percentage = 0.6
    candidate_two_percentage = 1 - candidate_one_percentage
    voter_ballot_selections = {}

    def load_voter_roll(self):
        self.voter_roll = []
        for voter_id in range(self.num_voters):
            voter_id = str(voter_id+1)
            name = 'Voter{}'.format(voter_id)
            self.voter_roll.append(Voter(voter_id, name, num_claim_tickets=1))
            
            self.voter_ballot_selections[voter_id] = {}
            for position in self.ballot.items:
                metadata = self.ballot.items[position]
                if int(voter_id)/self.num_voters <= self.candidate_one_percentage:
                    selected = [0]
                else:
                    selected = [1]
                self.voter_ballot_selections[voter_id][position] = selected

    def setup(self, *args, num_voters=100, **kwargs):
        self.num_voters = num_voters
        super().setup(*args, **kwargs)

    def begin_program(self):
        """
        Overriding flow of program since it doesnt require user interaction
        kwargs:
            selections   
        """
        self.last_time = datetime.now()
    
        utils.clear_screen()
        self.display_header()
        
        for voter in self.voter_roll:
            # get voter's pre-configured choices
            self.vote(selections=self.voter_ballot_selections[voter.id])

            if self.is_consensus_round():
                self.demonstrate_consensus(self.voter_authentication_booths, 'Voter Blockchain')
                self.demonstrate_consensus(self.voting_computers, 'Ballot Blockchain')
            utils.clear_screen()
            self.display_header()

        input("Press any key to continue")

        self.demonstrate_consensus(self.voter_authentication_booths, 'Voter Blockchain')
        self.demonstrate_consensus(self.voting_computers, 'Ballot Blockchain')        
        print("Election over! Results: ")
        self.display_results(nodes_in_sync=True)

    def display_menu(self):
        print('Simulating voting process')

    def get_menu_choice(self):
        return 1  # choice for voting

    def _authenticate_voter(self, voter_auth_booth):
        while self.current_voter_index < len(self.voter_roll):
            voter = self.voter_roll[self.current_voter_index]
            self.current_voter_index += 1
            authenticated = voter_auth_booth.authenticate_voter(voter.id)
            if not authenticated:
                print('Voter {} is not on voter roll'.format(voter.name))
                return None
            else:
                print('Authenticated voter {}'.format(voter.name))
                return voter.id