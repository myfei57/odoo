# Part of Odoo. See LICENSE file for full copyright and licensing details.
import ast

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.fields import Domain
from odoo.tools.misc import clean_context


class IrFilters(models.Model):
    _name = 'ir.filters'
    _description = 'Filter'
    _order = 'model_id, name, id desc'

    name = fields.Char(string='Filter Name', required=True)
    user_ids = fields.Many2many('res.users', string='Users', ondelete='cascade', help="The users the filter is shared with. If empty, the filter is shared with all users.", context={'active_test': False})
    domain = fields.Text(default='[]', required=True)
    context = fields.Text(default='{}', required=True)
    sort = fields.Char(default='[]', required=True)
    model_id = fields.Selection(selection='_list_all_models', string='Model', required=True)
    is_default = fields.Boolean(string='Default Filter')
    action_id = fields.Many2one('ir.actions.actions', string='Action', ondelete='cascade',
                                help="The menu action this filter applies to. "
                                     "When left empty the filter applies to all menus "
                                     "for this model.")
    embedded_action_id = fields.Many2one('ir.embedded.actions', help="The embedded action this filter is applied to", ondelete="cascade", index='btree_not_null')
    embedded_parent_res_id = fields.Integer(help="id of the record the filter should be applied to. Only used in combination with embedded actions")
    active = fields.Boolean(default=True)

    _get_filters_index = models.Index(
        '(model_id, action_id, embedded_action_id, embedded_parent_res_id)',
    )
    # The embedded_parent_res_id can only be defined when the embedded_action_id field is set.
    # As the embedded model is linked to only one res_model, It ensure the unicity of the filter regarding the
    # embedded_parent_res_model and the embedded_parent_res_id
    _check_res_id_only_when_embedded_action = models.Constraint(
        'CHECK(NOT (embedded_parent_res_id IS NOT NULL AND embedded_action_id IS NULL))',
        "Constraint to ensure that the embedded_parent_res_id is only defined when a top_action_id is defined.",
    )
    _check_sort_json = models.Constraint(
        "CHECK(sort IS NULL OR jsonb_typeof(sort::jsonb) = 'array')",
        "Invalid sort definition",
    )

    def _sanitize_shared_context(self):
        for shared_filter in self.filtered(lambda f: f.user_ids != self.env.user and f.context.strip()):
            context = ast.literal_eval(shared_filter.context.strip())
            cleaned = clean_context(context)
            defaults = context.keys() - cleaned.keys()
            if defaults:
                shared_filter.context = repr(cleaned)

    @api.model
    def _list_all_models(self):
        lang = self.env.lang or 'en_US'
        self.env.cr.execute(
            "SELECT model, COALESCE(name->>%s, name->>'en_US') FROM ir_model ORDER BY 2",
            [lang],
        )
        return self.env.cr.fetchall()

    def copy_data(self, default=None):
        vals_list = super().copy_data(default=default)
        # NULL Integer field value read as 0, wouldn't matter except in this case will trigger
        # check_res_id_only_when_embedded_action
        for vals in vals_list:
            if vals.get('embedded_parent_res_id') == 0:
                del vals['embedded_parent_res_id']
        return vals_list

    @api.model_create_multi
    def create(self, vals_list):
        filters = super().create(vals_list)
        filters._sanitize_shared_context()
        return filters

    def write(self, vals):
        new_filter = super().write(vals)
        self.check_access('write')
        if 'context' in vals or 'user_ids' in vals:
            self._sanitize_shared_context()
        return new_filter

    def _get_eval_domain(self) -> Domain:
        try:
            return Domain(ast.literal_eval(self.domain))
        except ValueError as e:
            raise ValueError(f"Invalid domain: {self.domain}") from e

    @api.model
    def _get_action_domain(self, action_id=None, embedded_action_id=None, embedded_parent_res_id=None) -> Domain:
        """Return a domain component for matching filters that are visible in the
           same context (menu/view) as the given action."""
        action_condition = Domain('action_id', 'in', [action_id or False, False])
        embedded_condition = Domain('embedded_action_id', '=', embedded_action_id or False)
        embedded_parent_res_id_condition = Domain('embedded_parent_res_id', '=', embedded_parent_res_id if embedded_action_id and embedded_parent_res_id else False)

        return action_condition & embedded_condition & embedded_parent_res_id_condition

    @api.model
    def get_filters(self, model, action_id=None, embedded_action_id=None, embedded_parent_res_id=None):
        """Obtain the list of filters available for the user on the given model.

        :param int model: id of model to find filters for
        :param action_id: optional ID of action to restrict filters to this action
            plus global filters. If missing only global filters are returned.
            The action does not have to correspond to the model, it may only be
            a contextual action.
        :return: list of :meth:`~osv.read`-like dicts containing the
            ``name``, ``is_default``, ``domain``, ``user_ids`` (m2m),
            ``action_id`` (m2o tuple), ``embedded_action_id`` (m2o tuple), ``embedded_parent_res_id``
            and ``context`` of the matching ``ir.filters``.
        """
        # available filters: private filters (user_ids=uids) and public filters (uids=NULL),
        # and filters for the action (action_id=action_id) or global (action_id=NULL)
        user_context = self.env['res.users'].context_get()
        action_domain = self._get_action_domain(action_id, embedded_action_id, embedded_parent_res_id)
        return self.with_context(user_context).search_read(
            action_domain & Domain('model_id', '=', model) & Domain('user_ids', 'in', [self.env.uid, False]),
            ['name', 'is_default', 'domain', 'context', 'user_ids', 'sort', 'embedded_action_id', 'embedded_parent_res_id'],
        )

    @api.model
    def create_filter(self, vals):
        embedded_action_id = vals.get('embedded_action_id')
        if not embedded_action_id and 'embedded_parent_res_id' in vals:
            del vals['embedded_parent_res_id']
        return self.create(vals)

    @api.model
    def preview_combined_domain(self, first_filter_id, second_filter_id, combinator):
        """Preview the domain combining two saved filters, without modifying them.

        The two filters must be readable by the current user (private filters
        of other users are therefore rejected, as ``search`` enforces the
        record rules), target the same model, and share the same action and
        embedded context scope. Their ``context`` and ``sort`` must be equal,
        otherwise the call is explicitly rejected instead of silently choosing
        one side. The two complete domains are combined using the regular
        :class:`~odoo.fields.Domain` semantics, preserving nested logic.

        This is a preview only: it never creates, overwrites or applies a
        filter, and it does not write on the source records. The returned
        domain is meant to be used in a regular search on the target model,
        which keeps applying the usual access rights and record rules; this
        method performs no such search and cannot bypass them.

        :param int first_filter_id: id of the first saved filter
        :param int second_filter_id: id of the second saved filter
        :param str combinator: ``"AND"`` for the intersection or ``"OR"`` for
            the union
        :return: a domain list usable to search the model shared by the two
            filters
        :rtype: list
        """
        if combinator not in ('AND', 'OR'):
            raise UserError(_(
                "Invalid operator %(combinator)s; only \"AND\" and \"OR\" can be used "
                "to combine saved filters.",
                combinator=combinator,
            ))

        # Go through search() so that access rights and record rules are applied:
        # a filter that is not visible to the current user is simply not found.
        filter_ids = [first_filter_id, second_filter_id]
        if not all(isinstance(filter_id, int) and not isinstance(filter_id, bool) for filter_id in filter_ids):
            raise UserError(_("The ids of the filters must be integers."))
        visible_filters = self.search([('id', 'in', list(set(filter_ids)))])
        filters_by_id = {filter_.id: filter_ for filter_ in visible_filters}
        if any(filter_id not in filters_by_id for filter_id in filter_ids):
            raise AccessError(_(
                "Some filters do not exist or are not visible to you, so they "
                "cannot be combined.",
            ))
        first_filter = filters_by_id[first_filter_id]
        second_filter = filters_by_id[second_filter_id]

        if first_filter.model_id != second_filter.model_id:
            raise UserError(_(
                "Filters can only be combined when they target the same model.",
            ))
        # The action and the embedded context scope must match exactly.
        for field_name in ('action_id', 'embedded_action_id', 'embedded_parent_res_id'):
            if first_filter[field_name] != second_filter[field_name]:
                raise UserError(_(
                    "Filters can only be combined when they apply to the same "
                    "action and embedded context.",
                ))

        # Parse context and sort as literals only; mismatches are rejected,
        # never resolved by silently picking one of the two sides.
        try:
            contexts = [ast.literal_eval(first_filter.context.strip() or '{}'),
                        ast.literal_eval(second_filter.context.strip() or '{}')]
            sorts = [ast.literal_eval(first_filter.sort.strip() or '[]'),
                     ast.literal_eval(second_filter.sort.strip() or '[]')]
        except (ValueError, SyntaxError, TypeError) as error:
            raise UserError(_(
                "The context or sort of one of the filters cannot be parsed.",
            )) from error
        if not all(isinstance(context, dict) for context in contexts):
            raise UserError(_("The context of a filter must be a dictionary."))
        if not all(isinstance(sort, list) for sort in sorts):
            raise UserError(_("The sort of a filter must be a list."))
        if not all(all(isinstance(key, str) for key in context) for context in contexts):
            raise UserError(_("The keys of a filter context must be strings."))
        if contexts[0] != contexts[1]:
            raise UserError(_(
                "The two filters define different contexts; align them before "
                "combining the filters.",
            ))
        if sorts[0] != sorts[1]:
            raise UserError(_(
                "The two filters define different sort orders; align them "
                "before combining the filters.",
            ))

        # Parse the domains with the existing safe literal/domain rules; no
        # arbitrary expression is ever evaluated.
        try:
            first_domain = first_filter._get_eval_domain()
            second_domain = second_filter._get_eval_domain()
        except (ValueError, SyntaxError, TypeError) as error:
            raise UserError(_(
                "The domain of one of the filters is invalid and cannot be "
                "combined.",
            )) from error

        if combinator == 'AND':
            combined_domain = first_domain & second_domain
        else:
            combined_domain = first_domain | second_domain

        # Make sure the combined conditions can actually be resolved against
        # the target model, using the current user's security (field access in
        # particular). This does not run any business search.
        target_model = self.env[first_filter.model_id].with_context(**contexts[0])
        # empty recordset: check the user has read rights on the model in general
        target_model.browse().check_access('read')
        try:
            combined_domain.validate(target_model)
        except (ValueError, TypeError) as error:
            raise UserError(_(
                "The combined domain cannot be applied to model %(model)s: %(error)s",
                model=first_filter.model_id,
                error=error,
            )) from error

        return [] if combined_domain.is_true() else list(combined_domain)
