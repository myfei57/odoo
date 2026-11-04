# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

import ast
import logging

from odoo import exceptions
from odoo.addons.base.tests.common import TransactionCaseWithUserDemo
from odoo.tests.common import TransactionCase, ADMIN_USER_ID, tagged

_logger = logging.getLogger(__name__)

def noid(seq):
    """ Removes values that are not relevant for the test comparisons """
    for d in seq:
        d.pop('id', None)
        d.pop('action_id', None)
        d.pop('embedded_action_id', None)
        d.pop('embedded_parent_res_id', None)
    return seq

class FiltersCase(TransactionCaseWithUserDemo):
    def setUp(self):
        super(FiltersCase, self).setUp()
        self.USER_NG = self.env['res.users'].name_search('demo')[0]
        self.USER_ID = self.USER_NG[0]

    def build(self, model, *args):
        Model = self.env[model].with_user(ADMIN_USER_ID)
        Model.create(args)


@tagged('at_install', '-post_install')  # LEGACY at_install
class TestGetFilters(FiltersCase):

    def test_own_filters(self):
        self.build(
            'ir.filters',
            dict(name='a', user_ids=[self.USER_ID], model_id='ir.filters'),
            dict(name='b', user_ids=[self.USER_ID], model_id='ir.filters'),
            dict(name='c', user_ids=[self.USER_ID], model_id='ir.filters'),
            dict(name='d', user_ids=[self.USER_ID], model_id='ir.filters'))

        filters = self.env['ir.filters'].with_user(self.USER_ID).get_filters('ir.filters')

        self.assertItemsEqual(noid(filters), [
            dict(name='a', is_default=False, user_ids=[self.USER_NG[0]], domain='[]', context='{}', sort='[]'),
            dict(name='b', is_default=False, user_ids=[self.USER_NG[0]], domain='[]', context='{}', sort='[]'),
            dict(name='c', is_default=False, user_ids=[self.USER_NG[0]], domain='[]', context='{}', sort='[]'),
            dict(name='d', is_default=False, user_ids=[self.USER_NG[0]], domain='[]', context='{}', sort='[]'),
        ])

    def test_global_filters(self):
        self.build(
            'ir.filters',
            dict(name='a', user_ids=[], model_id='ir.filters'),
            dict(name='b', user_ids=[], model_id='ir.filters'),
            dict(name='c', user_ids=[], model_id='ir.filters'),
            dict(name='d', user_ids=[], model_id='ir.filters'),
        )

        filters = self.env['ir.filters'].with_user(self.USER_ID).get_filters('ir.filters')

        self.assertItemsEqual(noid(filters), [
            dict(name='a', is_default=False, user_ids=[], domain='[]', context='{}', sort='[]'),
            dict(name='b', is_default=False, user_ids=[], domain='[]', context='{}', sort='[]'),
            dict(name='c', is_default=False, user_ids=[], domain='[]', context='{}', sort='[]'),
            dict(name='d', is_default=False, user_ids=[], domain='[]', context='{}', sort='[]'),
        ])

    def test_no_third_party_filters(self):
        self.build(
            'ir.filters',
            dict(name='a', user_ids=[], model_id='ir.filters'),
            dict(name='b', user_ids=[ADMIN_USER_ID], model_id='ir.filters'),
            dict(name='c', user_ids=[self.USER_ID], model_id='ir.filters'),
            dict(name='d', user_ids=[ADMIN_USER_ID], model_id='ir.filters'))

        filters = self.env['ir.filters'].with_user(self.USER_ID).get_filters('ir.filters')

        self.assertItemsEqual(noid(filters), [
            dict(name='a', is_default=False, user_ids=[], domain='[]', context='{}', sort='[]'),
            dict(name='c', is_default=False, user_ids=[self.USER_NG[0]], domain='[]', context='{}', sort='[]'),
        ])


@tagged('post_install', '-at_install', 'migration')
class TestAllFilters(TransactionCase):
    def check_filter(self, name, model, domain, aggregates, groupby, order, context):
        if groupby:
            try:
                Model = self.env[model].with_context(context)
                groupby = [groupby] if isinstance(groupby, str) else groupby
                groupby = [
                    f"{group_spec}:month" if (
                        ":" not in group_spec and
                        group_spec in Model._fields and
                        Model._fields[group_spec].type in ('date, datetime')
                    ) else group_spec
                    for group_spec in groupby
                ]
                Model.formatted_read_group(domain, groupby, aggregates, order=order)
            except ValueError as e:
                raise self.failureException("Test filter '%s' failed: %s" % (name, e)) from None
            except KeyError as e:
                raise self.failureException("Test filter '%s' failed: field or aggregate %s does not exist"% (name, e)) from None
        elif domain:
            try:
                self.env[model].with_context(context).search(domain, order=order)
            except ValueError as e:
                raise self.failureException("Test filter '%s' failed: %s" % (name, e)) from None
        else:
            _logger.info("No domain or group by in filter %s with model %s and context %s", name, model, context)

    def test_filters(self):
        for filter_ in self.env['ir.filters'].search([]):
            with self.subTest(name=filter_.name):
                context = ast.literal_eval(filter_.context)
                groupby = context.get('group_by')
                self.check_filter(
                    name=filter_.name,
                    model=filter_.model_id,
                    domain=filter_._get_eval_domain(),
                    aggregates=['__count'],
                    groupby=groupby,
                    order=','.join(ast.literal_eval(filter_.sort)),
                    context=context,
                )


@tagged('at_install', '-post_install')  # LEGACY at_install
class TestEmbeddedFilters(FiltersCase):

    def setUp(self):
        super(FiltersCase, self).setUp()
        self.USER_NG = self.env['res.users'].name_search('demo')[0]
        self.USER_ID = self.USER_NG[0]
        self.parent_action = self.env['ir.actions.act_window'].create({
            'name': 'ParentAction',
            'res_model': 'res.partner',
        })
        self.action_1 = self.env['ir.actions.act_window'].create({
            'name': 'Action1',
            'res_model': 'res.partner',
        })
        self.embedded_action_1 = self.env['ir.embedded.actions'].create({
            'name': 'EmbeddedAction1',
            'parent_res_model': 'res.partner',
            'parent_action_id': self.parent_action.id,
            'action_id': self.action_1.id,
        })
        self.embedded_action_2 = self.env['ir.embedded.actions'].create({
            'name': 'EmbeddedAction2',
            'parent_res_model': 'res.partner',
            'parent_action_id': self.parent_action.id,
            'action_id': self.action_1.id,
        })

    def test_global_filters_with_embedded_action(self):
        Filters = self.env['ir.filters'].with_user(self.USER_ID)
        Filters.create_filter({
            'name': 'a',
            'model_id': 'ir.filters',
            'user_ids': [],
            'is_default': True,
            'embedded_action_id': self.embedded_action_1.id,
            'embedded_parent_res_id': 1
        })
        Filters.create_filter({
            'name': 'b',
            'model_id': 'ir.filters',
            'user_ids': [self.USER_ID],
            'is_default': False,
            'embedded_action_id': self.embedded_action_2.id,
            'embedded_parent_res_id': 1
        })

        # If embedded_action_id and embedded_parent_res_id are set, should return the corresponding filter
        filters = self.env['ir.filters'].with_user(self.USER_ID).get_filters('ir.filters', embedded_action_id=self.embedded_action_1.id, embedded_parent_res_id=1)
        self.assertItemsEqual(noid(filters), [dict(name='a', is_default=True, user_ids=[], domain='[]', context='{}', sort='[]')])

        # Check that the filter is correctly linked to one embedded_parent_res_id and is not returned if another one is set
        filters = self.env['ir.filters'].with_user(self.USER_ID).get_filters('ir.filters', embedded_action_id=self.embedded_action_1.id, embedded_parent_res_id=2)
        self.assertItemsEqual(noid(filters), [])

        # Check that a shared filter can be fetched with another user
        filters = self.env['ir.filters'].with_user(ADMIN_USER_ID).get_filters('ir.filters', embedded_action_id=self.embedded_action_1.id, embedded_parent_res_id=1)
        self.assertItemsEqual(noid(filters), [dict(name='a', is_default=True, user_ids=[], domain='[]', context='{}', sort='[]')])

        # If embedded_action_id and embedded_parent_res_id are not set, should return no filters
        filters = self.env['ir.filters'].with_user(self.USER_ID).get_filters('ir.filters')
        self.assertItemsEqual(noid(filters), [])

    def test_global_filters_with_no_embedded_action(self):
        Filters = self.env['ir.filters'].with_user(self.USER_ID)
        filter_a = Filters.create_filter({
            'name': 'a',
            'model_id': 'ir.filters',
            'user_ids': [],
            'is_default': True,
            'embedded_action_id': False,
            'embedded_parent_res_id': 0,
        })
        filter_b = Filters.create_filter({
            'name': 'b',
            'model_id': 'ir.filters',
            'user_ids': [self.USER_ID],
            'is_default': True,
            'embedded_action_id': False,
            'embedded_parent_res_id': 1,
        })
        self.assertFalse(filter_a.embedded_action_id)
        self.assertFalse(filter_a.embedded_parent_res_id)
        self.assertFalse(filter_b.embedded_action_id)
        self.assertFalse(filter_b.embedded_parent_res_id)


@tagged('at_install', '-post_install')  # LEGACY at_install
class TestPreviewCombinedDomain(FiltersCase):

    def setUp(self):
        super().setUp()
        self.Filters = self.env['ir.filters'].with_user(ADMIN_USER_ID)
        self.action_1 = self.env['ir.actions.act_window'].create({
            'name': 'Action1',
            'res_model': 'res.partner',
        })
        self.action_2 = self.env['ir.actions.act_window'].create({
            'name': 'Action2',
            'res_model': 'res.partner',
        })
        self.embedded_action_1 = self.env['ir.embedded.actions'].create({
            'name': 'EmbeddedAction1',
            'parent_res_model': 'res.partner',
            'parent_action_id': self.action_1.id,
            'action_id': self.action_1.id,
        })
        self.embedded_action_2 = self.env['ir.embedded.actions'].create({
            'name': 'EmbeddedAction2',
            'parent_res_model': 'res.partner',
            'parent_action_id': self.action_1.id,
            'action_id': self.action_1.id,
        })

    def make_filter(self, name, domain='[]', context='{}', sort='[]',
                    user_ids=None, model='res.partner', **extra):
        return self.Filters.create(dict({
            'name': name,
            'model_id': model,
            'domain': domain,
            'context': context,
            'sort': sort,
            'user_ids': [] if user_ids is None else user_ids,
        }, **extra))

    def preview(self, first, second, combinator, user=None):
        Filters = self.env['ir.filters']
        if user is not None:
            Filters = Filters.with_user(user)
        return Filters.preview_combined_domain(first.id, second.id, combinator)

    def test_and_combination(self):
        first = self.make_filter('a', "[('name', '=', 'A')]")
        second = self.make_filter('b', "[('ref', '=', 'R')]")
        self.assertEqual(
            self.preview(first, second, 'AND'),
            ['&', ('name', '=', 'A'), ('ref', '=', 'R')],
        )

    def test_or_combination(self):
        first = self.make_filter('a', "[('name', '=', 'A')]")
        second = self.make_filter('b', "[('ref', '=', 'R')]")
        self.assertEqual(
            self.preview(first, second, 'OR'),
            ['|', ('name', '=', 'A'), ('ref', '=', 'R')],
        )

    def test_empty_domains(self):
        first = self.make_filter('a', '[]')
        second = self.make_filter('b', '[]')
        self.assertEqual(self.preview(first, second, 'AND'), [])
        self.assertEqual(self.preview(first, second, 'OR'), [])

    def test_empty_domain_with_leaf(self):
        empty = self.make_filter('empty', '[]')
        leaf = self.make_filter('leaf', "[('name', '=', 'A')]")
        # everything AND leaf -> leaf
        self.assertEqual(self.preview(empty, leaf, 'AND'), [('name', '=', 'A')])
        self.assertEqual(self.preview(leaf, empty, 'AND'), [('name', '=', 'A')])
        # everything OR leaf -> everything
        self.assertEqual(self.preview(empty, leaf, 'OR'), [])
        self.assertEqual(self.preview(leaf, empty, 'OR'), [])

    def test_nested_or_is_preserved(self):
        first = self.make_filter('a', "['|', ('name', '=', 'A'), ('ref', '=', 'R')]")
        second = self.make_filter('b', "[('is_company', '=', True)]")
        self.assertEqual(
            self.preview(first, second, 'OR'),
            ['|', '|', ('name', '=', 'A'), ('ref', '=', 'R'), ('is_company', '=', True)],
        )
        self.assertEqual(
            self.preview(first, second, 'AND'),
            ['&', '|', ('name', '=', 'A'), ('ref', '=', 'R'), ('is_company', '=', True)],
        )

    def test_nested_logic_with_negation(self):
        # a simple negation is normalized to the inverse operator by Domain
        first = self.make_filter('a', "['!', ('name', '=', 'A')]")
        second = self.make_filter('b', "[('is_company', '=', True)]")
        self.assertEqual(
            self.preview(first, second, 'AND'),
            ['&', ('name', '!=', 'A'), ('is_company', '=', True)],
        )
        # the negation node on a relational path is kept nested
        first_path = self.make_filter('c', "['!', ('parent_id.name', '=', 'A')]")
        self.assertEqual(
            self.preview(first_path, second, 'AND'),
            ['&', '!', ('parent_id.name', '=', 'A'), ('is_company', '=', True)],
        )

    def test_same_filter_twice(self):
        filter_ = self.make_filter('selfcombo', "[('name', '=', 'A')]")
        self.assertEqual(
            self.preview(filter_, filter_, 'AND'),
            ['&', ('name', '=', 'A'), ('name', '=', 'A')],
        )

    def test_context_mismatch_rejected(self):
        first = self.make_filter('a', context="{'group_by': ['name']}")
        second = self.make_filter('b', context='{}')
        with self.assertRaises(exceptions.UserError):
            self.preview(first, second, 'AND')
        # neither side is silently chosen: the reverse call is rejected too
        with self.assertRaises(exceptions.UserError):
            self.preview(second, first, 'AND')

    def test_sort_mismatch_rejected(self):
        first = self.make_filter('a', sort='[["name", "desc"]]')
        second = self.make_filter('b', sort='[]')
        with self.assertRaises(exceptions.UserError):
            self.preview(first, second, 'AND')

    def test_equal_context_and_sort_accepted(self):
        first = self.make_filter('a', domain="[('name', '=', 'A')]",
                                 context="{'group_by': ['name']}", sort='[["ref", "asc"]]')
        second = self.make_filter('b', domain="[('ref', '=', 'R')]",
                                  context="{'group_by': ['name']}", sort='[["ref", "asc"]]')
        self.assertEqual(
            self.preview(first, second, 'AND'),
            ['&', ('name', '=', 'A'), ('ref', '=', 'R')],
        )

    def test_different_model_rejected(self):
        first = self.make_filter('a', model='res.partner')
        second = self.make_filter('b', model='res.users')
        with self.assertRaises(exceptions.UserError):
            self.preview(first, second, 'AND')

    def test_action_mismatch_rejected(self):
        first = self.make_filter('a', action_id=self.action_1.id)
        second = self.make_filter('b', action_id=self.action_2.id)
        with self.assertRaises(exceptions.UserError):
            self.preview(first, second, 'AND')

    def test_same_action_accepted(self):
        first = self.make_filter('a', "[('name', '=', 'A')]", action_id=self.action_1.id)
        second = self.make_filter('b', "[('ref', '=', 'R')]", action_id=self.action_1.id)
        self.assertEqual(
            self.preview(first, second, 'AND'),
            ['&', ('name', '=', 'A'), ('ref', '=', 'R')],
        )

    def test_embedded_action_mismatch_rejected(self):
        first = self.make_filter('a', embedded_action_id=self.embedded_action_1.id,
                                 embedded_parent_res_id=1)
        second = self.make_filter('b', embedded_action_id=self.embedded_action_2.id,
                                  embedded_parent_res_id=1)
        with self.assertRaises(exceptions.UserError):
            self.preview(first, second, 'AND')

    def test_embedded_parent_res_id_mismatch_rejected(self):
        first = self.make_filter('a', embedded_action_id=self.embedded_action_1.id,
                                 embedded_parent_res_id=1)
        second = self.make_filter('b', embedded_action_id=self.embedded_action_1.id,
                                  embedded_parent_res_id=2)
        with self.assertRaises(exceptions.UserError):
            self.preview(first, second, 'AND')

    def test_same_embedded_scope_accepted(self):
        first = self.make_filter('a', "[('name', '=', 'A')]",
                                 embedded_action_id=self.embedded_action_1.id,
                                 embedded_parent_res_id=1)
        second = self.make_filter('b', "[('ref', '=', 'R')]",
                                  embedded_action_id=self.embedded_action_1.id,
                                  embedded_parent_res_id=1)
        self.assertEqual(
            self.preview(first, second, 'OR'),
            ['|', ('name', '=', 'A'), ('ref', '=', 'R')],
        )

    def test_invalid_combinator_rejected(self):
        first = self.make_filter('a')
        second = self.make_filter('b')
        for combinator in ('XOR', 'and', 'or', '', None):
            with self.assertRaises(exceptions.UserError):
                self.preview(first, second, combinator)

    def test_invalid_domain_rejected(self):
        valid = self.make_filter('valid')
        for invalid_domain in (
            "['&', ('name', '=', 'A')]",          # malformed domain
            "[('name', 'bogus_op', 'A')]",         # invalid operator
            "[('not_a_field_xyz', '=', 'A')]",     # unknown field
            "__import__('os').system('echo hi')",  # arbitrary expression
            "1 / 0",                               # arbitrary expression
        ):
            with self.subTest(invalid_domain=invalid_domain):
                invalid = self.make_filter('invalid', invalid_domain)
                with self.assertRaises(exceptions.UserError):
                    self.preview(valid, invalid, 'AND')

    def test_invalid_context_rejected(self):
        # an unparseable context can only reach the method through corrupted
        # stored data, since create/write already sanitize shared contexts
        valid = self.make_filter('validctx')
        invalid = self.make_filter('badctx')
        self.env.cr.execute(
            "UPDATE ir_filters SET context = %s WHERE id = %s",
            ("{'group_by': undefined_var}", invalid.id),
        )
        invalid.invalidate_recordset()
        with self.assertRaises(exceptions.UserError):
            self.preview(valid, invalid, 'AND')

    def test_private_filter_not_visible(self):
        mine = self.make_filter('mine', "[('name', '=', 'A')]", user_ids=[self.USER_ID])
        admin_only = self.make_filter('admin', "[('ref', '=', 'R')]", user_ids=[ADMIN_USER_ID])
        # demo cannot see a filter private to another user, in either order
        with self.assertRaises(exceptions.AccessError):
            self.preview(mine, admin_only, 'AND', user=self.USER_ID)
        with self.assertRaises(exceptions.AccessError):
            self.preview(admin_only, mine, 'AND', user=self.USER_ID)
        # nonexistent filters are rejected the same way
        missing = self.env['ir.filters'].browse(987654321)
        with self.assertRaises(exceptions.AccessError):
            self.preview(mine, missing, 'AND', user=self.USER_ID)

    def test_shared_filters_visible(self):
        # globally shared filter
        shared = self.make_filter('shared', "[('name', '=', 'A')]", user_ids=[])
        # filter explicitly shared with demo
        shared_with_demo = self.make_filter('demoshared', "[('ref', '=', 'R')]",
                                            user_ids=[self.USER_ID])
        self.assertEqual(
            self.preview(shared, shared_with_demo, 'OR', user=self.USER_ID),
            ['|', ('name', '=', 'A'), ('ref', '=', 'R')],
        )

    def test_admin_sees_all_filters(self):
        mine = self.make_filter('mine', "[('name', '=', 'A')]", user_ids=[self.USER_ID])
        admin_only = self.make_filter('admin', "[('ref', '=', 'R')]", user_ids=[ADMIN_USER_ID])
        self.assertEqual(
            self.preview(mine, admin_only, 'AND', user=ADMIN_USER_ID),
            ['&', ('name', '=', 'A'), ('ref', '=', 'R')],
        )

    def test_preview_does_not_modify_filters(self):
        first = self.make_filter('nomod_a', "[('name', '=', 'A')]")
        second = self.make_filter('nomod_b', "[('ref', '=', 'R')]")
        filters = first + second
        before = filters.read(['name', 'domain', 'context', 'sort', 'model_id',
                               'action_id', 'embedded_action_id', 'embedded_parent_res_id'])
        before_count = self.Filters.search_count([])
        self.preview(first, second, 'OR')
        self.preview(first, second, 'AND')
        self.invalidate_recordset()
        self.assertEqual(
            filters.read(['name', 'domain', 'context', 'sort', 'model_id',
                          'action_id', 'embedded_action_id', 'embedded_parent_res_id']),
            before,
        )
        # no filter has been created or deleted
        self.assertEqual(self.Filters.search_count([]), before_count)

    def test_preview_result_is_usable_in_search(self):
        first = self.make_filter('a', "[('name', 'ilike', 'A')]")
        second = self.make_filter('b', "['|', ('ref', '=', 'R'), ('is_company', '=', True)]")
        combined = self.preview(first, second, 'OR', user=self.USER_ID)
        # the demo user can run a regular search with the preview; record rules
        # of res.partner are still applied by the search itself
        self.env['res.partner'].with_user(self.USER_ID).search(combined, limit=5)

    def test_preview_does_not_inject_privileged_operators(self):
        first = self.make_filter('a', "[('name', '=', 'A')]")
        second = self.make_filter('b', "['|', ('ref', '=', 'R'), ('is_company', '=', True)]")
        combined = self.preview(first, second, 'AND')
        operators = {leaf[1] for leaf in combined if isinstance(leaf, tuple)}
        self.assertFalse(operators & {'any!', 'not any!'})
