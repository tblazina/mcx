from typing import List

import astor
import networkx as nx

from mcx.core.nodes import Argument, RandVar, Transformation, Var


class GraphicalModel(nx.DiGraph):
    """Represents a probabilistic graphical model.

    Nodes in a graphical model can represent a constant, a random variable or
    function, a factor, a deterministic transformation. Edges indicate the
    dependency relationships between variables.

    `GraphicalModel` is the central object of the library. It is generated by
    parsing the model definition, can be modified at runtime or by the
    compilers. Source code for the model's logpdf, prior and posterior samples
    are generated by traversing this graph.
    """

    def __init__(self):
        super(GraphicalModel, self).__init__()

    # @property
    # def nodes(self):
    # """We overload networkx's `Graph.nodes` to make it easier to retrieve
    # the nodes' parameters.  """
    # nodes = NodeView(self)
    # self.__dict__['nodes'] = nodes
    # return nodes

    def do(self, **kwargs) -> "GraphicalModel":
        """Apply the do-operator to the graph and return a copy.

        The do-operator `do(var=x)` removes the edges coming from `var`'s
        parents and sets its value to x.

        Examples
        --------

        >>> model.do(sigma=10).forward()
        ... {'weight': 1.0, 'y': 2}
        """
        new_model = self.copy()
        for name, value in kwargs:
            if name not in self.nodes:
                raise NameError("The specified node {} does not exist.")

            new_model.nodes[name]["content"] = Var(name, value, False)

            predecessors = nx.predecessors(new_model, name)
            for predecessor in predecessors:
                new_model.remove_edge(predecessor, name)

        # The do-operator will likely separate the graph in different
        # connected components. We only keep the component(s) that contain
        # returned nodes.
        nodes_to_keep: List[str] = []
        connected_components = nx.algorithms.weakly_connected_components(new_model)
        for component in connected_components:
            has_returned = sum([new_model.nodes[node]["content"].is_returned for node in component])
            if has_returned:
                nodes_to_keep += [node for node in component]

        new_model = nx.subgraph(new_model, nodes_to_keep)

        return new_model

    def markov_blanket(self, var_name):
        """Return a node's Markov blanket.

        The Markov blanket of a node is the set of its parents, its children
        and its children's parents.
        """
        if var_name not in self.nodes:
            raise NameError("The specified node {} does not exist.")

        parents = list(self.predecessors(var_name))
        children = list(self.succ(var_name))

        children_parents = []
        for child in children:
            children_parents += list(self.predecessors(child))
        children_parents = [p for p in children_parents if p != var_name]

        return parents + children + children_parents

    def add_argument(self, name):
        self.add_node(name, content=Argument(name))

    def add_variable(self, name, value, is_returned=False):
        self.add_node(name, content=Var(name, value, is_returned))

    def add_transformation(self, name, expression, args, is_returned=False):
        for arg in args:
            if isinstance(arg, str):
                if arg in self.nodes:
                    self.add_edge(arg, name)
                else:
                    raise SyntaxError(
                        "The variable {} referenced in the exression {} ~ {} is undefined".format(
                            arg, name, astor.code_gen.to_source(expression)
                        )
                    )
        self.add_node(name, content=Transformation(name, expression, args, is_returned))

    def add_randvar(self, name, distribution, args, is_returned=False):
        for arg in args:
            if isinstance(arg, str):
                if arg in self.nodes:
                    self.add_edge(arg, name)
                else:
                    raise SyntaxError(
                        "The variable {} referenced in the expression {} ~ {} is undefined".format(
                            arg, name, astor.code_gen.to_source(distribution)
                        )
                    )
        self.add_node(name, content=RandVar(name, distribution, args, is_returned))

    def mark_as_returned(self, name):
        self.nodes[name]["content"].is_returned = True
