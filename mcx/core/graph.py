import ast
from typing import List

import astor
import networkx as nx

from mcx.core.nodes import Argument, RandVar, Transformation, Var
from mcx.core.utils import relabel_arguments


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
        super().__init__()

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
        for name, value in kwargs.items():
            if name not in self.nodes:
                raise NameError("The specified node {} does not exist.")

            ast_value = ast.Constant(value=value)
            new_model.nodes[name]["content"] = Var(name, ast_value, False)

            predecessors = new_model.predecessors(name)
            to_remove = []
            for predecessor in predecessors:
                to_remove.append(predecessor)

            for predecessor in to_remove:
                new_model.remove_edge(predecessor, name)

        # The do-operator will likely separate the graph in different
        # connected components. We only keep the component(s) that contain
        # returned nodes.
        nodes_to_keep: List[str] = []
        connected_components = nx.algorithms.weakly_connected_components(new_model)
        for component in connected_components:
            has_returned = sum(
                [new_model.nodes[node]["content"].is_returned for node in component]
            )
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

    @property
    def arguments(self):
        """Returns the list of arguments to the model definition function."""
        args = [n for n in self.nodes if isinstance(self.nodes[n]["content"], Argument)]
        return args

    @property
    def returned_variables(self):
        """Returns the list of the variables returned by the model definition function."""
        args = [n for n in self.nodes if self.nodes[n]["content"].is_returned is True]
        return args

    @property
    def variables(self):
        """Returns the random and deterministic variables.
        """
        args = [
            n
            for n in self.nodes
            if isinstance(self.nodes[n]["content"], RandVar)
            or isinstance(self.nodes[n]["content"], Transformation)
        ]
        return args

    @property
    def posterior_variables(self):
        """Returns the list of the random variables whose posterior
        distribution we want to sample.
        """
        args = [
            n
            for n in self.nodes
            if isinstance(self.nodes[n]["content"], RandVar)
            and not self.nodes[n]["content"].is_returned
        ]
        return args

    def add_argument(self, name, value=None):
        self.add_node(name, content=Argument(name, value))

    def add_variable(self, name, value, is_returned=False):
        self.add_node(name, content=Var(name, value, is_returned))

    def add_transformation(self, name, expression, args, is_returned=False):
        for arg in args:
            if isinstance(arg, str):
                if arg in self.nodes:
                    self.add_edge(arg, name)
                else:
                    raise SyntaxError(
                        "The variable {} referenced in the expression {} ~ {} is undefined".format(
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

    def merge_models(self, var_name, model_graph, args):
        """Merge a model used in a random variable assignment.
        """
        # The returned node now become a standard node
        name_returned = model_graph.returned_variables[0]
        model_graph.nodes[name_returned]["content"].is_returned = False

        # We first rename the nodes by appending the name of the
        # model to the variables names. This prevents overlap
        # when the same variable name has been used in multiple
        # model definitions.
        # The returned variable of the model being merged is rename
        # to the variable being assigned in the current model.
        mapping = {
            name: name + "_{}".format(model_graph.name) for name in model_graph.nodes
        }
        mapping.update({name_returned: var_name})
        model_graph = nx.relabel_nodes(model_graph, mapping)

        # update the nodes' internal names
        for name, node in model_graph.nodes(data=True):
            node["content"].name = name

        # Update the name of the arguments in the graph being merged.
        for _, content in model_graph.nodes(data=True):
            node = content["content"]
            if isinstance(node, Transformation) or isinstance(node, RandVar):
                node.args = [
                    mapping[arg] if isinstance(arg, str) else arg for arg in node.args
                ]
                if isinstance(node, Transformation):
                    relabel_arguments(node.expression, mapping)
                else:
                    distribution_args = []
                    for arg in node.distribution.args:
                        if isinstance(arg, ast.Name):
                            arg = ast.Name(id=mapping[arg.id], ctx=ast.Load())
                        distribution_args.append(arg)
                    node.distribution.args = distribution_args

        # Update the arguments with their value if provided
        for i, arg in enumerate(model_graph.arguments):
            if len(args) - 1 >= i:
                model_graph.remove_node(arg)
                if isinstance(args[i], int):
                    value = ast.Constant(value=args[i])
                elif isinstance(args[i], str):
                    value = ast.Name(id=args[i], ctx=ast.Load())
                model_graph.add_variable(arg, value)
            else:
                if model_graph.nodes[arg]["content"].default_value is None:
                    raise TypeError(
                        "{} missing one require positional argument: '{}'".format(
                            model_graph.name, arg
                        )
                    )

        # Beware that the merged graph takes the name of the first
        # argument's. It is important to keep it this way to keep
        # the name hierarchy.
        return nx.compose(model_graph, self)
